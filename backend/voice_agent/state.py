"""Per-call state shared by the realtime session, tools, and lifecycle hooks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from livekit.agents import BackgroundAudioPlayer, JobContext

from .config import AgentConfig
from .persistence import BackendClient
from .reservation import ReservationDraft

logger = logging.getLogger("voice-agent")

BACKGROUND_AUDIO_CLOSE_TIMEOUT_SECONDS = 1.5
PENDING_WRITES_CLOSE_TIMEOUT_SECONDS = 2.0
TRANSFER_CLOSE_TIMEOUT_SECONDS = 1.0

CallDirection = Literal["inbound", "outbound", "web"]
CallStatus = Literal["active", "transferring", "completed", "transferred", "voicemail", "failed"]
OutboundCallResult = Literal["greet", "engaged", "ended"]
TERMINAL_STATUSES: set[CallStatus] = {
    "completed",
    "transferred",
    "voicemail",
    "failed",
}


@dataclass(slots=True)
class CallState:
    """Mutable state shared by tools, events, and lifecycle callbacks."""

    config: AgentConfig
    backend: BackendClient
    job_ctx: JobContext | None
    direction: CallDirection
    participant_identity: str
    session_id: str | None = None
    phone_number: str | None = None
    customer_name: str = "there"
    call_log_id: int | None = None
    status: CallStatus = "active"
    outcome: str | None = None
    last_user_text: str = ""
    last_assistant_text: str = ""
    reservation: ReservationDraft = field(default_factory=ReservationDraft)
    background_audio: BackgroundAudioPlayer | None = None
    transfer_finished: asyncio.Event = field(default_factory=asyncio.Event)
    pending_writes: set[asyncio.Task[Any]] = field(default_factory=set)
    cleanup_started: bool = False

    def __post_init__(self) -> None:
        if self.phone_number and self.reservation.contact_phone is None:
            self.reservation.contact_phone = self.phone_number
        if self.customer_name.casefold() != "there" and self.reservation.guest_name is None:
            self.reservation.guest_name = self.customer_name

    def mark_terminal(self, status: CallStatus, outcome: str | None = None) -> None:
        if self.status in TERMINAL_STATUSES:
            return
        self.status = status
        self.outcome = outcome

    def schedule(self, operation: Coroutine[Any, Any, Any]) -> None:
        if self.cleanup_started:
            operation.close()
            return

        task = asyncio.create_task(operation)
        self.pending_writes.add(task)

        def _finished(completed: asyncio.Task[Any]) -> None:
            self.pending_writes.discard(completed)
            if completed.cancelled():
                return
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.warning(
                    "Background persistence task failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(_finished)

    async def aclose(self, reason: str = "") -> None:
        if self.cleanup_started:
            return
        self.cleanup_started = True

        was_transferring = self.status == "transferring"
        if self.status == "active":
            self.mark_terminal("completed", reason or "call ended")

        try:
            if self.background_audio is not None:
                try:
                    await asyncio.wait_for(
                        self.background_audio.aclose(),
                        timeout=BACKGROUND_AUDIO_CLOSE_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    logger.warning("Timed out while closing background audio")
                except Exception:
                    logger.warning("Failed to close background audio", exc_info=True)
                finally:
                    self.background_audio = None

            if self.pending_writes:
                pending = list(self.pending_writes)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=PENDING_WRITES_CLOSE_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

            if was_transferring:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.transfer_finished.wait(),
                        timeout=TRANSFER_CLOSE_TIMEOUT_SECONDS,
                    )
                if self.status == "transferring":
                    self.mark_terminal("failed", "transfer did not finish before shutdown")
        finally:
            # Always release the HTTP connector, even when the job runner cancels
            # this cleanup because its own shutdown deadline has been reached.
            try:
                await asyncio.wait_for(
                    self.backend.aclose(),
                    timeout=1,
                )
            except TimeoutError:
                logger.warning("Timed out while closing the backend client")
