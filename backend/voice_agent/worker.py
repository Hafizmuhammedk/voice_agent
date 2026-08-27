"""LiveKit worker registration, participant routing, and SIP lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    cli,
)

from .call_tools import terminate_live_call
from .config import (
    VALID_DIRECTION_HINTS,
    as_bool,
    clean_text,
    load_agent_config,
    normalize_phone,
    parse_job_metadata,
    positive_int,
)
from .persistence import BackendClient
from .session import (
    VoiceAgent,
    attach_session_events,
    create_session,
    greet_caller,
    make_room_options,
    start_background_audio,
)
from .state import CallDirection, CallState, OutboundCallResult

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("voice-agent")
DISPATCH_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "general-assistant")


def _opaque_participant_identity(ctx: JobContext) -> str:
    job_id = re.sub(r"[^A-Za-z0-9]", "", str(getattr(ctx.job, "id", "call")))
    return f"callee-{job_id[-16:] or 'call'}"


async def place_outbound_call(
    ctx: JobContext,
    session: AgentSession[CallState],
    state: CallState,
    phone_number: str,
) -> OutboundCallResult:
    """Dial a SIP participant and return as soon as the answered caller joins."""
    trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
    if not trunk_id:
        logger.error("Outbound call requested but SIP_OUTBOUND_TRUNK_ID is not configured")
        state.mark_terminal("failed", "missing outbound SIP trunk")
        return "ended"

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                participant_identity=state.participant_identity,
                participant_name="Phone caller",
                wait_until_answered=True,
            )
        )
        await asyncio.wait_for(
            ctx.wait_for_participant(identity=state.participant_identity),
            timeout=10,
        )
    except api.SipCallError as error:
        logger.error(
            "Outbound SIP call failed: code=%s status=%s",
            error.sip_status_code,
            error.sip_status,
        )
        state.mark_terminal("failed", "outbound SIP call failed")
        return "ended"
    except TimeoutError:
        logger.error("The answered SIP participant did not join the call room")
        state.mark_terminal("failed", "outbound SIP participant missing")
        return "ended"
    except Exception:
        logger.exception("Outbound call setup failed")
        state.mark_terminal("failed", "outbound call setup failed")
        return "ended"

    logger.info("Outbound caller answered and joined; starting the greeting immediately")
    return "greet"


server = AgentServer()


@server.rtc_session(agent_name=DISPATCH_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    metadata = parse_job_metadata(ctx.job.metadata)
    is_self_review = as_bool(metadata.get("is_self_review"), False)
    direction_hint = str(metadata.get("direction", "")).strip().lower()
    requested_phone = normalize_phone(metadata.get("phone_number"))
    raw_phone_present = bool(metadata.get("phone_number"))

    if direction_hint not in VALID_DIRECTION_HINTS:
        logger.error("direction must be inbound, outbound, web, or omitted")
        ctx.shutdown(reason="invalid call direction")
        return

    if raw_phone_present and requested_phone is None:
        logger.error("phone_number must use E.164 format, for example +15105550123")
        ctx.shutdown(reason="invalid outbound phone number")
        return

    if not is_self_review and direction_hint == "outbound" and requested_phone is None:
        logger.error("Outbound dispatch metadata requires an E.164 phone_number")
        ctx.shutdown(reason="missing outbound phone number")
        return

    is_outbound = not is_self_review and (
        direction_hint == "outbound" or (requested_phone is not None and direction_hint == "")
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    if is_outbound:
        direction: CallDirection = "outbound"
        participant_identity = _opaque_participant_identity(ctx)
        phone_number = requested_phone
    else:
        participant = await ctx.wait_for_participant()
        participant_identity = participant.identity
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            direction = "inbound"
            phone_number = normalize_phone(participant.attributes.get("sip.phoneNumber"))
        else:
            direction = "web"
            phone_number = None

    config = load_agent_config(metadata)
    state = CallState(
        config=config,
        backend=BackendClient(),
        job_ctx=ctx,
        direction=direction,
        participant_identity=participant_identity,
        session_id=clean_text(metadata.get("session_id"), "", max_length=48) or None,
        phone_number=phone_number,
        customer_name=clean_text(metadata.get("customer_name"), "there", max_length=120),
        call_log_id=positive_int(metadata.get("call_log_id")),
    )
    session = create_session(config, state)
    attach_session_events(session, state)

    async def _cleanup(reason: str = "") -> None:
        async def _finish() -> None:
            if state.session_id is not None and state.backend.enabled:
                final_status = "failed" if state.status == "failed" else "ended"
                try:
                    await asyncio.wait_for(
                        state.backend.update_session_status(state.session_id, final_status),
                        timeout=2,
                    )
                except TimeoutError:
                    logger.warning("Timed out while saving the final session status")
            # AgentSession registers its own shutdown callback and is closed by
            # LiveKit before custom cleanup callbacks execute.
            await state.aclose(reason)

        try:
            await asyncio.wait_for(_finish(), timeout=7)
        except TimeoutError:
            logger.warning("Voice-agent cleanup reached its seven-second safety limit")

    ctx.add_shutdown_callback(_cleanup)

    await session.start(
        agent=VoiceAgent(state),
        room=ctx.room,
        room_options=make_room_options(
            participant_identity,
            noise_suppression_level=state.config.noise_suppression_level,
        ),
    )
    if state.session_id is not None and state.backend.enabled:
        await state.backend.update_session_status(state.session_id, "active")

    outbound_result: OutboundCallResult = "greet"
    if is_outbound:
        if phone_number is None:
            state.mark_terminal("failed", "missing outbound phone number")
            await terminate_live_call(
                ctx,
                session,
                state.outcome or "missing outbound phone number",
            )
            return
        outbound_result = await place_outbound_call(ctx, session, state, phone_number)
        if outbound_result == "ended":
            await terminate_live_call(
                ctx,
                session,
                state.outcome or "outbound call ended",
            )
            return

    if outbound_result == "greet":
        await greet_caller(session, state)
    await start_background_audio(state, session)


def main() -> None:
    cli.run_app(server)
