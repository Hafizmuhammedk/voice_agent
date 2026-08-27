"""Validated public API contracts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
PROTECTED_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore|bypass|disable|override).{0,30}(?:system|security|safety|rules|instructions)"
    r"|(?:reveal|show|print|expose).{0,30}(?:api key|secret|system prompt|internal prompt)",
    re.IGNORECASE,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Personality(StrEnum):
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    CALM = "calm"
    CONCISE = "concise"
    ENERGETIC = "energetic"


class SessionStatus(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"
    FAILED = "failed"


def validate_custom_instructions(value: str) -> str:
    value = value.strip()
    if PROTECTED_INSTRUCTION_PATTERN.search(value):
        raise ValueError("custom instructions cannot override protected system rules")
    return value


class UserCreate(ApiModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class UserResponse(ApiModel):
    id: str
    display_name: str
    created_at: datetime


class UserCreatedResponse(UserResponse):
    api_token: str = Field(description="Shown once. Store it securely.")


class AgentSettingsInput(ApiModel):
    voice_id: str | None = Field(default=None, min_length=1, max_length=128)
    language: str = Field(default="en-US", min_length=2, max_length=20)
    personality: Personality = Personality.FRIENDLY
    speaking_speed: float = Field(default=1.0, ge=0.7, le=1.3)
    custom_instructions: str = Field(default="", max_length=4000)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        value = value.strip().replace("_", "-")
        if not LANGUAGE_PATTERN.fullmatch(value):
            raise ValueError("language must be a BCP-47 code such as en-US")
        return value

    @field_validator("voice_id")
    @classmethod
    def clean_voice(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("custom_instructions")
    @classmethod
    def protect_system_rules(cls, value: str) -> str:
        return validate_custom_instructions(value)


class AgentSettingsUpdate(ApiModel):
    voice_id: str | None = Field(default=None, min_length=1, max_length=128)
    language: str | None = Field(default=None, min_length=2, max_length=20)
    personality: Personality | None = None
    speaking_speed: float | None = Field(default=None, ge=0.7, le=1.3)
    custom_instructions: str | None = Field(default=None, max_length=4000)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().replace("_", "-")
        if not LANGUAGE_PATTERN.fullmatch(value):
            raise ValueError("language must be a BCP-47 code such as en-US")
        return value

    @field_validator("voice_id")
    @classmethod
    def clean_voice(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("custom_instructions")
    @classmethod
    def protect_system_rules(cls, value: str | None) -> str | None:
        return validate_custom_instructions(value) if value is not None else None


class AgentCreate(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    settings: AgentSettingsInput = Field(default_factory=AgentSettingsInput)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class AgentUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class AgentSettingsResponse(ApiModel):
    provider: str
    model: str
    voice_id: str
    language: str
    personality: Personality
    speaking_speed: float
    custom_instructions: str
    updated_at: datetime


class AgentResponse(ApiModel):
    id: str
    name: str
    settings: AgentSettingsResponse
    created_at: datetime
    updated_at: datetime


class VoiceResponse(ApiModel):
    id: str
    name: str
    provider: str
    model: str
    languages: list[str]
    supports_speed: bool


class CreateSessionRequest(ApiModel):
    agent_id: str = Field(min_length=1, max_length=48)
    temporary_settings: AgentSettingsUpdate | None = None


class SessionResponse(ApiModel):
    id: str
    agent_id: str
    room_name: str
    participant_identity: str
    status: SessionStatus
    configuration_snapshot: dict[str, object]
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class SessionCreatedResponse(SessionResponse):
    server_url: str
    participant_token: str
    token_expires_at: datetime


class OutboundCallRequest(ApiModel):
    agent_id: str = Field(min_length=1, max_length=48)
    phone_number: str = Field(min_length=8, max_length=16)
    customer_name: str = Field(default="there", min_length=1, max_length=120)

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        value = value.strip()
        if not E164_PATTERN.fullmatch(value):
            raise ValueError("phone number must use E.164 format, such as +919876543210")
        return value

    @field_validator("customer_name")
    @classmethod
    def clean_customer_name(cls, value: str) -> str:
        return value.strip()


class OutboundCallResponse(ApiModel):
    session_id: str
    dispatch_id: str
    room_name: str
    status: SessionStatus


class PhoneVerificationRequest(ApiModel):
    phone_number: str = Field(min_length=8, max_length=16)

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        value = value.strip()
        if not E164_PATTERN.fullmatch(value):
            raise ValueError("phone number must use E.164 format, such as +919876543210")
        return value


class PhoneVerificationStarted(ApiModel):
    phone_number: str
    validation_code: str | None = None
    status: str = "pending"


class PhoneVerificationStatus(ApiModel):
    phone_number: str
    verified: bool
    status: str


class PhoneVerificationPolicy(ApiModel):
    available: bool
    required: bool
    manual_verification_required: bool


class LiveKitTokenRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=48)


class LiveKitTokenResponse(ApiModel):
    session_id: str
    room_name: str
    server_url: str
    participant_token: str
    expires_at: datetime


class SessionStatusUpdate(ApiModel):
    status: SessionStatus


class HealthResponse(ApiModel):
    status: str
    service: str
