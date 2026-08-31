"""Validated environment and dispatch configuration for each call."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("voice-agent")

DEFAULT_VOICE_ID = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
DEFAULT_TIMEZONE = "Asia/Kolkata"
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
SUPPORTED_LANGUAGE_CODES = {"ar", "da", "de", "en", "es", "fr", "nl", "pt", "sv"}
VALID_DIRECTION_HINTS = {"", "inbound", "outbound", "web"}
SUPPORTED_PERSONALITIES = {
    "friendly",
    "professional",
    "casual",
    "calm",
    "concise",
    "energetic",
}

DEFAULT_BUSINESS_INSTRUCTIONS = """Help callers with general questions, explanations, planning, troubleshooting, and practical tasks.
Give accurate, useful answers and ask one concise clarifying question when a request is ambiguous.
If a request needs live information, private data, or an external action that no available tool can provide, explain that limitation clearly.
Never claim that an external action was completed unless an available tool explicitly confirms it."""


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Validated per-call configuration from environment and dispatch metadata."""

    agent_name: str
    company_name: str
    business_instructions: str
    provider: str
    voice_id: str
    temperature: float
    personality: str
    speaking_speed: float
    language: str
    timezone: str
    enable_background_audio: bool
    aec_warmup_seconds: float
    human_transfer_number: str | None
    noise_suppression_level: float = 0.7

    @property
    def language_code(self) -> str:
        return self.language.split("-", 1)[0].lower()


def clean_text(value: Any, default: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()[:max_length]


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def as_bool(value: Any, default: bool) -> bool:
    parsed = parse_bool(value)
    return default if parsed is None else parsed


def parse_bounded_float(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if number > 0 else None


def normalize_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s().-]", "", value)
    return normalized if E164_PATTERN.fullmatch(normalized) else None


def parse_job_metadata(raw_metadata: Any) -> dict[str, Any]:
    """Parse trusted dispatch metadata without logging caller data."""
    if not raw_metadata:
        return {}
    if isinstance(raw_metadata, dict):
        return dict(raw_metadata)
    if not isinstance(raw_metadata, str) or len(raw_metadata) > 64_000:
        logger.warning("Ignoring invalid job metadata")
        return {}
    if raw_metadata.startswith("+"):
        return {"phone_number": raw_metadata}
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed JSON job metadata")
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring non-object job metadata")
        return {}
    return parsed


def _config_candidates(
    metadata: dict[str, Any],
    keys: tuple[str, ...],
    env_name: str | None = None,
) -> list[Any]:
    """Return nested, top-level, then environment candidates for validation."""
    nested = metadata.get("agent_config")
    sources = (nested, metadata) if isinstance(nested, dict) else (metadata,)
    candidates: list[Any] = []
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            candidates.append(value)
    if env_name is not None:
        env_value = os.getenv(env_name)
        if env_value is not None and env_value.strip():
            candidates.append(env_value)
    return candidates


def _metadata_has_config_key(metadata: dict[str, Any], keys: tuple[str, ...]) -> bool:
    nested = metadata.get("agent_config")
    sources = (nested, metadata) if isinstance(nested, dict) else (metadata,)
    return any(key in source for source in sources for key in keys)


def _first_config_text(
    candidates: list[Any],
    default: str,
    *,
    max_length: int,
) -> str:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:max_length]
    return default


def _first_config_bool(candidates: list[Any], default: bool) -> bool:
    for candidate in candidates:
        parsed = parse_bool(candidate)
        if parsed is not None:
            return parsed
    return default


def _first_config_float(
    candidates: list[Any],
    default: float,
    low: float,
    high: float,
) -> float:
    for candidate in candidates:
        parsed = parse_bounded_float(candidate, low, high)
        if parsed is not None:
            return parsed
    return default


def load_agent_config(metadata: dict[str, Any]) -> AgentConfig:
    """Resolve validated config using nested metadata, top-level metadata, then env."""
    agent_name = _first_config_text(
        _config_candidates(metadata, ("agent_name",), "VOICE_AGENT_NAME"),
        "Alex",
        max_length=80,
    )
    company_name = _first_config_text(
        _config_candidates(metadata, ("company_name",), "COMPANY_NAME"),
        "the support team",
        max_length=120,
    )
    customer_name = clean_text(
        metadata.get("customer_name"),
        "there",
        max_length=120,
    )
    instruction_keys = ("custom_instructions", "instructions", "system_prompt")
    instruction_env = (
        None if _metadata_has_config_key(metadata, instruction_keys) else "AGENT_INSTRUCTIONS"
    )
    instruction_template = _first_config_text(
        _config_candidates(metadata, instruction_keys, instruction_env),
        DEFAULT_BUSINESS_INSTRUCTIONS,
        max_length=16_000,
    )
    business_instructions = (
        instruction_template.replace("{customer_name}", customer_name)
        .replace("{agent_name}", agent_name)
        .replace("{company_name}", company_name)
    )

    language = "en-US"
    for candidate in _config_candidates(metadata, ("language",), "AGENT_LANGUAGE"):
        if not isinstance(candidate, str):
            logger.warning("Ignoring a non-text agent language")
            continue
        normalized_language = candidate.strip()[:20].replace("_", "-")
        if normalized_language.split("-", 1)[0].lower() in SUPPORTED_LANGUAGE_CODES:
            language = normalized_language
            break
        logger.warning("Unsupported agent language %r; trying the next fallback", candidate)

    timezone_name = DEFAULT_TIMEZONE
    for candidate in _config_candidates(metadata, ("timezone",), "AGENT_TIMEZONE"):
        if not isinstance(candidate, str):
            logger.warning("Ignoring a non-text agent timezone")
            continue
        candidate_name = candidate.strip()[:64]
        try:
            ZoneInfo(candidate_name)
        except (ValueError, ZoneInfoNotFoundError):
            logger.warning("Unknown agent timezone %r; trying the next fallback", candidate)
            continue
        timezone_name = candidate_name
        break

    transfer_number = None
    for candidate in _config_candidates(
        metadata,
        ("human_transfer_number", "transfer_number"),
        "HUMAN_TRANSFER_NUMBER",
    ):
        transfer_number = normalize_phone(candidate)
        if transfer_number is not None:
            break
        logger.warning("Ignoring a human transfer number that is not E.164")

    personality = "friendly"
    for candidate in _config_candidates(metadata, ("personality",), "AGENT_PERSONALITY"):
        if isinstance(candidate, str) and candidate.strip().lower() in SUPPORTED_PERSONALITIES:
            personality = candidate.strip().lower()
            break
        logger.warning("Ignoring unsupported agent personality")

    if _config_candidates(metadata, ("prompt_id",)):
        logger.warning(
            "prompt_id was supplied but no prompt database is installed; "
            "send the resolved prompt as instructions or system_prompt"
        )

    return AgentConfig(
        agent_name=agent_name,
        company_name=company_name,
        business_instructions=business_instructions,
        provider=_first_config_text(
            _config_candidates(metadata, ("provider",), "VOICE_PROVIDER"),
            "livekit-inference",
            max_length=40,
        ),
        voice_id=_first_config_text(
            _config_candidates(
                metadata,
                ("voice_id", "voice", "tts_voice_id"),
                "CARTESIA_VOICE_ID",
            ),
            DEFAULT_VOICE_ID,
            max_length=128,
        ),
        temperature=_first_config_float(
            _config_candidates(
                metadata,
                ("temperature", "llm_temperature"),
                "LLM_TEMPERATURE",
            ),
            0.3,
            0.0,
            2.0,
        ),
        personality=personality,
        speaking_speed=_first_config_float(
            _config_candidates(
                metadata,
                ("speaking_speed",),
                "SPEAKING_SPEED",
            ),
            1.0,
            0.7,
            1.3,
        ),
        language=language,
        timezone=timezone_name,
        enable_background_audio=_first_config_bool(
            _config_candidates(
                metadata,
                ("enable_background_audio",),
                "ENABLE_BACKGROUND_AUDIO",
            ),
            False,
        ),
        aec_warmup_seconds=_first_config_float(
            _config_candidates(
                metadata,
                ("aec_warmup_seconds",),
                "AEC_WARMUP_SECONDS",
            ),
            0.5,
            0.0,
            5.0,
        ),
        human_transfer_number=transfer_number,
        noise_suppression_level=_first_config_float(
            _config_candidates(
                metadata,
                ("noise_suppression_level",),
                "NOISE_SUPPRESSION_LEVEL",
            ),
            0.7,
            0.0,
            1.0,
        ),
    )


# Backward-compatible aliases for integrations that imported private helpers.
_clean_text = clean_text
_as_bool = as_bool
_positive_int = positive_int
_normalize_phone = normalize_phone
