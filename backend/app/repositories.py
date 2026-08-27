"""Database query boundaries used by application services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db.models import Agent, AgentSettings, User, VoiceSession


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_token_hash(self, token_hash: str) -> User | None:
        result = await self.session.scalar(select(User).where(User.api_token_hash == token_hash))
        return result


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _owned_query(user_id: int):
        return select(Agent).options(selectinload(Agent.settings)).where(Agent.user_id == user_id)

    async def create(self, agent: Agent, settings: AgentSettings) -> Agent:
        agent.settings = settings
        self.session.add(agent)
        await self.session.flush()
        return agent

    async def list_owned(self, user_id: int) -> list[Agent]:
        result = await self.session.scalars(
            self._owned_query(user_id).order_by(Agent.created_at.desc())
        )
        return list(result.unique())

    async def get_owned(self, user_id: int, public_id: str) -> Agent | None:
        return await self.session.scalar(
            self._owned_query(user_id).where(Agent.public_id == public_id)
        )

    async def delete(self, agent: Agent) -> None:
        await self.session.delete(agent)


class VoiceSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, voice_session: VoiceSession) -> VoiceSession:
        self.session.add(voice_session)
        await self.session.flush()
        return voice_session

    async def get_owned(self, user_id: int, public_id: str) -> VoiceSession | None:
        return await self.session.scalar(
            select(VoiceSession).where(
                VoiceSession.user_id == user_id,
                VoiceSession.public_id == public_id,
            )
        )

    async def get_by_public_id(self, public_id: str) -> VoiceSession | None:
        return await self.session.scalar(
            select(VoiceSession).where(VoiceSession.public_id == public_id)
        )
