"""Typed application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_VOICE_LANGUAGE_CODES = frozenset(
    {"ar", "da", "de", "en", "es", "fr", "nl", "pt", "sv"}
)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Voice Agent API"
    app_environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./voice_agent.db"

    livekit_url: str
    livekit_api_key: SecretStr
    livekit_api_secret: SecretStr
    livekit_agent_name: str = "general-assistant"
    livekit_token_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    sip_outbound_trunk_id: str | None = None

    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None
    twilio_trial_mode: bool = True

    cartesia_voice_id: str = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
    voice_provider: str = "livekit-inference"
    voice_model: str = "cartesia/sonic-3"
    backend_api_token: SecretStr | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("livekit_url")
    @classmethod
    def validate_livekit_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("LIVEKIT_URL must start with ws:// or wss://")
        return value

    @field_validator("livekit_agent_name", "cartesia_voice_id")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("sip_outbound_trunk_id")
    @classmethod
    def clean_optional_trunk_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("twilio_account_sid", "twilio_phone_number")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("twilio_auth_token")
    @classmethod
    def clean_optional_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        return value if value.get_secret_value().strip() else None

    @field_validator("livekit_api_key", "livekit_api_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("LiveKit credentials must not be empty")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()  # pyright: ignore[reportCallIssue]
