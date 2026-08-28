"""Voice-pipeline provider boundary used by the realtime runtime."""

from __future__ import annotations

import os
from typing import Protocol

from livekit.agents import AgentSession, TurnHandlingOptions, inference
from livekit.plugins import deepgram

from .config import AgentConfig
from .state import CallState

DEFAULT_LIVEKIT_LLM_MODEL = "google/gemini-2.5-flash-lite"

# Low-latency defaults. Keep VAD silence at or above 0.25 seconds because the
# LiveKit TurnDetector rejects smaller values. Flux can start speculative LLM
# work before this timeout, while the shorter final timeout avoids a long pause
# after a caller has clearly stopped speaking.
ENGLISH_EOT_TIMEOUT_MS = 750
PHONE_MAX_ENDPOINTING_DELAY = 0.65
WEB_MAX_ENDPOINTING_DELAY = 0.8
CARTESIA_MAX_BUFFER_DELAY_MS = 0


class VoiceProvider(Protocol):
    name: str

    def create_session(self, config: AgentConfig, state: CallState) -> AgentSession[CallState]: ...


class LiveKitInferenceProvider:
    """Direct Deepgram STT with LiveKit-hosted Gemini LLM and Cartesia TTS."""

    name = "livekit-inference"

    def create_session(self, config: AgentConfig, state: CallState) -> AgentSession[CallState]:
        llm_model = os.getenv(
            "LIVEKIT_LLM_MODEL",
            DEFAULT_LIVEKIT_LLM_MODEL,
        ).strip() or DEFAULT_LIVEKIT_LLM_MODEL
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
        if not deepgram_api_key:
            raise RuntimeError(
                "Deepgram is not configured. Set DEEPGRAM_API_KEY in backend/.env."
            )

        language = config.language_code
        is_phone_call = state.direction in {"inbound", "outbound"}
        if language == "en":
            stt_provider = deepgram.STTv2(
                model="flux-general-en",
                eager_eot_threshold=0.35,
                eot_threshold=0.55,
                eot_timeout_ms=ENGLISH_EOT_TIMEOUT_MS,
                api_key=deepgram_api_key,
            )
        else:
            stt_provider = deepgram.STT(
                model="nova-3",
                language=config.language,
                api_key=deepgram_api_key,
            )
        tts_speed = config.speaking_speed * (0.9 if language == "ar" else 1.0)

        return AgentSession(
            userdata=state,
            stt=stt_provider,
            llm=inference.LLM(
                model=llm_model,
                extra_kwargs={
                    "max_completion_tokens": 220,
                    "temperature": config.temperature,
                },
            ),
            tts=inference.TTS(
                model="cartesia/sonic-3",
                voice=config.voice_id,
                language=language,
                extra_kwargs={
                    "speed": tts_speed,
                    "volume": 1.0,
                    "add_timestamps": True,
                    # Send each completed early phrase to Cartesia immediately.
                    # The prompt deliberately produces a short, meaningful first
                    # sentence so audio can start while Gemini keeps generating.
                    "max_buffer_delay_ms": CARTESIA_MAX_BUFFER_DELAY_MS,
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
                    "min_delay": 0.25,
                    "max_delay": (
                        PHONE_MAX_ENDPOINTING_DELAY
                        if is_phone_call
                        else WEB_MAX_ENDPOINTING_DELAY
                    ),
                    "alpha": 0.8 if is_phone_call else 0.75,
                },
                interruption={
                    "enabled": True,
                    "mode": "adaptive",
                    "min_duration": 0.25,
                    "min_words": 0,
                    "resume_false_interruption": False,
                    "false_interruption_timeout": 0.75,
                },
                preemptive_generation={
                    "enabled": True,
                    # Feed streaming LLM text into streaming TTS immediately. Audio
                    # is prepared while the turn is confirmed, then played without
                    # waiting for the complete LLM response.
                    "preemptive_tts": True,
                    "max_speech_duration": 8.0,
                    "max_retries": 2,
                },
            ),
            # Allow a bounded sequence of tool calls for multi-step requests while
            # preserving the existing STT, LLM, and TTS model selections.
            max_tool_steps=5,
            min_consecutive_speech_delay=0.0,
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
