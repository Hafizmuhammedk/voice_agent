"""Realtime model pipeline and full-duplex room/session behavior."""

from __future__ import annotations

import logging

from livekit.agents import (
    Agent,
    AgentFalseInterruptionEvent,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    llm,
    room_io,
)
from livekit.agents.inference.interruption import OverlappingSpeechEvent
from livekit.plugins import ai_coustics

from .call_tools import (
    CALL_FAREWELLS,
    has_explicit_end_call_request,
    terminate_live_call,
    transfer_to_human,
)
from .config import AgentConfig
from .prompts import (
    asked_for_final_reservation_confirmation,
    asked_to_confirm_contact_number,
    build_agent_instructions,
    build_reservation_turn_hint,
    is_affirmative_reply,
    resolve_relative_reservation_dates,
)
from .providers import get_voice_provider
from .state import TERMINAL_STATUSES, CallState

logger = logging.getLogger("voice-agent")

AMBIENT_OFFICE_VOLUME = 1.5
THINKING_KEYBOARD_VOLUME = 0.5
THINKING_KEYBOARD2_VOLUME = 0.5


class VoiceAgent(Agent):
    """Role-conditioned agent with only the tools available for this call."""

    def __init__(self, state: CallState) -> None:
        # Hang-up is handled deterministically in on_user_turn_completed. Sentiment
        # is also not model-controlled: false sentiment tool calls delayed normal
        # polite closings and could disrupt the conversation state.
        tools: list[llm.Tool | llm.Toolset] = []
        if (
            state.direction in {"inbound", "outbound"}
            and state.config.human_transfer_number is not None
        ):
            tools.append(transfer_to_human)

        super().__init__(
            instructions=build_agent_instructions(state.config, state),
            tools=tools,
        )
        self._call_state = state

    async def on_user_turn_completed(
        self,
        turn_ctx: llm.ChatContext,
        new_message: llm.ChatMessage,
    ) -> None:
        """Give the LLM resolved reservation dates without changing the transcript."""
        user_text = new_message.text_content or ""
        self._call_state.last_user_text = user_text

        if (
            has_explicit_end_call_request(user_text)
            and self._call_state.job_ctx is not None
        ):
            farewell = CALL_FAREWELLS.get(
                self._call_state.config.language_code,
                CALL_FAREWELLS["en"],
            ).format(company_name=self._call_state.config.company_name)
            await self._speak_closing_and_end(
                farewell,
                "caller requested end call",
            )
            raise llm.StopResponse()

        is_affirmative = is_affirmative_reply(user_text)
        reservation = self._call_state.reservation
        if (
            is_affirmative
            and self._call_state.direction == "outbound"
            and reservation.ready_for_final_confirmation
            and asked_for_final_reservation_confirmation(
                self._call_state.last_assistant_text
            )
            and self._call_state.job_ctx is not None
        ):
            reservation.final_confirmed = True
            closing = (
                "Thank you for confirming your reservation request with "
                f"{self._call_state.config.company_name}. The hotel team can use your "
                "confirmed contact number for any follow-up. Have a wonderful day. Goodbye."
            )
            await self._speak_closing_and_end(
                closing,
                "caller confirmed reservation request",
            )
            raise llm.StopResponse()

        contact_phone = self._call_state.phone_number
        contact_confirmed_now = (
            is_affirmative
            and self._call_state.direction == "outbound"
            and contact_phone is not None
            and asked_to_confirm_contact_number(self._call_state.last_assistant_text)
        )
        if contact_confirmed_now and contact_phone is not None:
            reservation.contact_confirmed = True
            turn_ctx.add_message(
                role="system",
                content=(
                    "Internal reservation-state update; never quote this note: the caller "
                    f"confirmed the application-entered contact number ending in "
                    f"{contact_phone[-4:]}. Do not ask for the contact "
                    "number again. Continue with the next missing reservation detail."
                ),
            )

        resolved_dates = resolve_relative_reservation_dates(
            self._call_state.config,
            self._call_state.last_assistant_text,
            user_text,
        )
        if "check-in" in resolved_dates:
            reservation.check_in = resolved_dates["check-in"]
        if "check-out" in resolved_dates:
            reservation.check_out = resolved_dates["check-out"]

        structured_updates = reservation.capture_answer(
            self._call_state.last_assistant_text,
            user_text,
        )
        if structured_updates:
            turn_ctx.add_message(
                role="system",
                content=(
                    "Internal reservation-state update; never quote this note: "
                    f"{reservation.prompt_summary()} Do not ask for retained fields again. "
                    "Ask only for the next missing field."
                ),
            )

        hint = build_reservation_turn_hint(
            self._call_state.config,
            self._call_state.last_assistant_text,
            user_text,
        )
        if hint:
            hint += f" {reservation.prompt_summary()}"
            turn_ctx.add_message(role="system", content=hint)

    async def _speak_closing_and_end(self, message: str, reason: str) -> None:
        """Play the complete closing before terminating the dedicated call room."""
        job_ctx = self._call_state.job_ctx
        if job_ctx is None:
            return
        self._call_state.mark_terminal("completed", reason)
        speech = self.session.say(message, allow_interruptions=False)
        await speech.wait_for_playout()
        await terminate_live_call(
            job_ctx,
            self.session,
            self._call_state.outcome or reason,
        )


def create_session(config: AgentConfig, state: CallState) -> AgentSession[CallState]:
    """Create a low-latency, PersonaPlex-style cascaded speech pipeline.

    This uses LiveKit Cloud models rather than NVIDIA PersonaPlex itself. Input and
    output stay active concurrently, adaptive interruption handles barge-in, and
    eager turn prediction reduces response delay.
    """
    return get_voice_provider(config.provider).create_session(config, state)


def attach_session_events(session: AgentSession[CallState], state: CallState) -> None:
    """Capture current LiveKit events without blocking the audio pipeline."""

    @session.on("conversation_item_added")
    def _on_conversation_item(event: ConversationItemAddedEvent) -> None:
        item = event.item
        if not isinstance(item, llm.ChatMessage):
            return
        text = item.text_content
        if not text or item.role not in {"user", "assistant"}:
            return

        if item.role == "user":
            state.last_user_text = text
        else:
            state.last_assistant_text = text

        if state.backend.enabled and state.call_log_id is not None:
            state.schedule(
                state.backend.post_json(
                    "/api/transcripts",
                    {
                        "call_log_id": state.call_log_id,
                        "speaker": "customer" if item.role == "user" else "agent",
                        "text": text,
                        "timestamp": item.created_at,
                        "emotion": "neutral",
                        "interrupted": item.interrupted,
                        "item_id": item.id,
                    },
                )
            )

    @session.on("overlapping_speech")
    def _on_overlapping_speech(event: OverlappingSpeechEvent) -> None:
        logger.debug(
            "Overlapping speech: interruption=%s delay=%.3f duration=%.3f",
            event.is_interruption,
            event.detection_delay,
            event.total_duration,
        )

    @session.on("agent_false_interruption")
    def _on_false_interruption(event: AgentFalseInterruptionEvent) -> None:
        logger.debug("False interruption handled: resumed=%s", event.resumed)

    @session.on("error")
    def _on_error(event: ErrorEvent) -> None:
        logger.error(
            "Voice pipeline error from %s: %s",
            type(event.source).__name__,
            event.error,
        )

    @session.on("close")
    def _on_close(event: CloseEvent) -> None:
        failed = event.error is not None or event.reason.value == "error"
        if failed and state.status not in TERMINAL_STATUSES:
            state.mark_terminal("failed", event.reason.value)
        elif state.status == "active":
            state.mark_terminal("completed", event.reason.value)


def make_room_options(
    participant_identity: str,
    *,
    noise_suppression_level: float = 0.7,
) -> room_io.RoomOptions:
    """Keep microphone input and synthesized output active at the same time."""
    return room_io.RoomOptions(
        participant_identity=participant_identity,
        # Keep the room available when a worker is restarted unexpectedly. LiveKit
        # can then reconnect/re-dispatch instead of forcing the browser out with
        # ROOM_DELETED. Empty rooms are still removed by LiveKit's departure timeout,
        # and the end-call tool explicitly deletes the room for a real hang-up.
        delete_room_on_close=False,
        audio_input=room_io.AudioInputOptions(
            pre_connect_audio=True,
            auto_gain_control=True,
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_S,
                model_parameters=ai_coustics.ModelParameters(
                    enhancement_level=noise_suppression_level,
                ),
            ),
        ),
        audio_output=room_io.AudioOutputOptions(),
        text_output=room_io.TextOutputOptions(sync_transcription=True),
    )


async def start_background_audio(
    state: CallState,
    session: AgentSession[CallState],
) -> None:
    if not state.config.enable_background_audio or state.job_ctx is None:
        return
    player = BackgroundAudioPlayer(
        # Ambient audio is continuous and does not duck while the caller speaks.
        # Keep it well below speech so phone speakers do not feed it back into STT.
        ambient_sound=AudioConfig(
            BuiltinAudioClip.OFFICE_AMBIENCE,
            volume=AMBIENT_OFFICE_VOLUME,
        ),
        thinking_sound=[
            AudioConfig(
                BuiltinAudioClip.KEYBOARD_TYPING,
                volume=THINKING_KEYBOARD_VOLUME,
            ),
            AudioConfig(
                BuiltinAudioClip.KEYBOARD_TYPING2,
                volume=THINKING_KEYBOARD2_VOLUME,
            ),
        ],
    )
    try:
        await player.start(room=state.job_ctx.room, agent_session=session)
    except Exception:
        logger.warning("Background audio could not be started", exc_info=True)
        await player.aclose()
        return
    state.background_audio = player


async def greet_caller(session: AgentSession[CallState], state: CallState) -> None:
    if state.config.language_code == "en":
        name = state.customer_name.strip()
        name_prefix = (
            f" {name},"
            if state.direction == "outbound" and name.casefold() != "there"
            else ","
        )
        call_phrase = "calling from" if state.direction == "outbound" else "with"
        await session.say(
            f"Hello{name_prefix} this is {state.config.agent_name} {call_phrase} "
            f"{state.config.company_name}. I'm your virtual front desk assistant, and I can help "
            "with reservations, room information, hotel amenities, and guest services. "
            "What can I help you with today?",
            allow_interruptions=True,
        )
        return

    # Keep configured non-English languages localized through the existing LLM.
    await session.generate_reply(
        instructions=(
            f"Greet the caller warmly in {state.config.language}. Say that the hotel is "
            f"{state.config.company_name} and that you are {state.config.agent_name}, its virtual "
            "front desk assistant. Briefly say you help with reservations, rooms, amenities, and "
            "guest services. End with exactly one question asking how you can help."
        ),
        allow_interruptions=True,
        tool_choice="none",
    )
