"""Relational persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    api_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    agents: Mapped[list[Agent]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[VoiceSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="agents")
    settings: Mapped[AgentSettings] = relationship(
        back_populates="agent", cascade="all, delete-orphan", uselist=False
    )
    sessions: Mapped[list[VoiceSession]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentSettings(Base):
    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="livekit-inference")
    model: Mapped[str] = mapped_column(String(80), default="cartesia/sonic-3")
    company_name: Mapped[str] = mapped_column(String(120), default="Your hotel")
    voice_id: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(20), default="en-US")
    personality: Mapped[str] = mapped_column(String(24), default="friendly")
    speaking_speed: Mapped[float] = mapped_column(Float, default=1.0)
    custom_instructions: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    agent: Mapped[Agent] = relationship(back_populates="settings")


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    __table_args__ = (
        Index("ix_voice_sessions_user_status", "user_id", "status"),
        Index("ix_voice_sessions_agent_created", "agent_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    room_name: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    participant_identity: Mapped[str] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")
    agent: Mapped[Agent] = relationship(back_populates="sessions")
