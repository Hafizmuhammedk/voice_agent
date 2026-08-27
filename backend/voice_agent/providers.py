"""Voice-pipeline provider boundary used by the realtime runtime."""

from __future__ import annotations

import os
from typing import Any, Protocol

from livekit.agents import AgentSession, TurnHandlingOptions, inference
from livekit.plugins import google

from .config import AgentConfig
from .state import CallState

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class VoiceProvider(Protocol):
    name: str

    def create_session(self, config: AgentConfig, state: CallState) -> AgentSession[CallState]: ...


class LiveKitInferenceProvider:
    """Streaming LiveKit STT/TTS with Gemini using the operator's Google API key."""

    name = "livekit-inference"

    def create_session(self, config: AgentConfig, state: CallState) -> AgentSession[CallState]:
        google_api_key = (
            os.getenv("GOOGLE_API_KEY", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
        )
        if not google_api_key:
            raise RuntimeError(
                "Gemini is not configured. Set GOOGLE_API_KEY in backend/.env."
            )
        gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        if not gemini_model:
            gemini_model = DEFAULT_GEMINI_MODEL

        language = config.language_code
        is_phone_call = state.direction in {"inbound", "outbound"}
        stt_model = "deepgram/flux-general-en" if language == "en" else "deepgram/nova-3"
        stt_options: dict[str, Any] = {"eager_eot_threshold": 0.35} if language == "en" else {}
        tts_speed = config.speaking_speed * (0.9 if language == "ar" else 1.0)

        return AgentSession(
            userdata=state,
            stt=inference.STT(
                model=stt_model,
                language=language,
                extra_kwargs=stt_options,
            ),
            llm=google.LLM(
                model=gemini_model,
                api_key=google_api_key,
                max_output_tokens=220,
                temperature=config.temperature,
            ),
            tts=inference.TTS(
                model="cartesia/sonic-3",
                voice=config.voice_id,
                language=language,
                extra_kwargs={
                    "speed": tts_speed,
                    "volume": 1.0,
                    "add_timestamps": True,
                },
            ),
            vad=inference.VAD(
                min_speech_duration=0.06 if is_phone_call else 0.08,
                # LiveKit's TurnDetector requires at least 0.25 seconds.
                min_silence_duration=0.25 if is_phone_call else 0.28,
                # Preserve more leading audio and accept quieter narrow-band
                # telephone speech without changing the configured STT model.
                prefix_padding_duration=0.35 if is_phone_call else 0.2,
                activation_threshold=0.32 if is_phone_call else 0.42,
            ),
            turn_handling=TurnHandlingOptions(
                turn_detection=inference.TurnDetector(),
                endpointing={
                    "mode": "dynamic",
                    "min_delay": 0.25 if is_phone_call else 0.35,
                    "max_delay": 1.2 if is_phone_call else 2.0,
                    "alpha": 0.75 if is_phone_call else 0.7,
                },
                interruption={
                    "enabled": True,
                    "mode": "adaptive",
                    "min_duration": 0.25,
                    "min_words": 0,
                    "resume_false_interruption": False,
                    "false_interruption_timeout": 1.0,
                },
                preemptive_generation={
                    "enabled": True,
                    # Keep the LLM preemptive, but wait for a confirmed turn before
                    # starting TTS. This prevents partial words and replayed audio.
                    "preemptive_tts": False,
                    "max_speech_duration": 8.0,
                    "max_retries": 2,
                },
            ),
            # Allow a bounded sequence of tool calls for multi-step requests while
            # preserving the existing STT, LLM, and TTS model selections.
            max_tool_steps=5,
            use_tts_aligned_transcript=True,
            aec_warmup_duration=config.aec_warmup_seconds,
        )


_PROVIDERS: dict[str, VoiceProvider] = {
    LiveKitInferenceProvider.name: LiveKitInferenceProvider(),
}


def get_voice_provider(name: str) -> VoiceProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as error:
        raise ValueError(f"unsupported voice provider: {name}") from error
