"""Application services for users, configurable agents, and voice sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from livekit import api
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .core.config import SUPPORTED_VOICE_LANGUAGE_CODES, AppSettings
from .core.security import create_api_token, hash_api_token
from .db.models import Agent, AgentSettings, User, VoiceSession
from .repositories import AgentRepository, UserRepository, VoiceSessionRepository
from .schemas import (
    AgentCreate,
    AgentResponse,
    AgentSettingsInput,
    AgentSettingsResponse,
    AgentSettingsUpdate,
    AgentUpdate,
    LiveKitTokenResponse,
    OutboundCallRequest,
    OutboundCallResponse,
    Personality,
    PhoneVerificationRequest,
    PhoneVerificationStarted,
    PhoneVerificationStatus,
    SessionCreatedResponse,
    SessionResponse,
    SessionStatus,
    UserCreatedResponse,
    UserResponse,
    VoiceResponse,
)

logger = logging.getLogger("voice-agent.backend")


class ResourceNotFoundError(Exception):
    pass


class InvalidConfigurationError(Exception):
    pass


class InvalidSessionStateError(Exception):
    pass


class OutboundDispatchError(Exception):
    pass


class PhoneVerificationError(Exception):
    pass


class PhoneVerificationRateLimitError(Exception):
    pass


_verification_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_verification_attempts_lock = Lock()
_VERIFICATION_WINDOW_SECONDS = 600
_VERIFICATION_MAX_ATTEMPTS = 3


class PhoneVerificationService:
    """Verify trial recipients without exposing Twilio credentials to the browser."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def _client(self) -> Client:
        if self.settings.twilio_account_sid is None or self.settings.twilio_auth_token is None:
            raise InvalidConfigurationError("Twilio number verification is not configured")
        return Client(
            self.settings.twilio_account_sid,
            self.settings.twilio_auth_token.get_secret_value(),
        )

    @staticmethod
    def _enforce_rate_limit(user: User, phone_number: str) -> None:
        now = time.monotonic()
        keys = (f"user:{user.public_id}", f"phone:{phone_number}")
        with _verification_attempts_lock:
            for key in keys:
                attempts = _verification_attempts[key]
                while attempts and now - attempts[0] >= _VERIFICATION_WINDOW_SECONDS:
                    attempts.popleft()
                if len(attempts) >= _VERIFICATION_MAX_ATTEMPTS:
                    raise PhoneVerificationRateLimitError(
                        "Too many verification attempts. Try again in a few minutes."
                    )
            for key in keys:
                _verification_attempts[key].append(now)

    async def start(
        self,
        user: User,
        payload: PhoneVerificationRequest,
    ) -> PhoneVerificationStarted:
        if self.settings.twilio_trial_mode:
            raise InvalidConfigurationError(
                "Twilio trial accounts do not support API verification. "
                "Add the destination under Verified Caller IDs in the Twilio Console."
            )
        client = self._client()
        try:
            existing = await asyncio.to_thread(
                client.outgoing_caller_ids.list,
                phone_number=payload.phone_number,
                limit=1,
            )
            if existing:
                return PhoneVerificationStarted(
                    phone_number=payload.phone_number,
                    status="verified",
                )
        except TwilioRestException as error:
            logger.warning(
                "twilio_verification_failed user_id=%s twilio_code=%s",
                user.public_id,
                error.code,
            )
            if error.code == 10002:
                raise InvalidConfigurationError(
                    "Twilio trial accounts do not support API verification. "
                    "Add the destination under Verified Caller IDs in the Twilio Console."
                ) from error
            raise PhoneVerificationError(
                "Twilio could not start verification for this number."
            ) from error
        except Exception as error:
            logger.exception("twilio_verification_failed user_id=%s", user.public_id)
            raise PhoneVerificationError(
                "Twilio number verification is temporarily unavailable."
            ) from error

        self._enforce_rate_limit(user, payload.phone_number)
        try:
            validation = await asyncio.to_thread(
                client.validation_requests.create,
                phone_number=payload.phone_number,
                friendly_name=f"Voice agent user {user.public_id[-8:]}",
            )
        except TwilioRestException as error:
            logger.warning(
                "twilio_verification_failed user_id=%s twilio_code=%s",
                user.public_id,
                error.code,
            )
            raise PhoneVerificationError(
                "Twilio could not start verification for this number."
            ) from error
        except Exception as error:
            logger.exception("twilio_verification_failed user_id=%s", user.public_id)
            raise PhoneVerificationError(
                "Twilio number verification is temporarily unavailable."
            ) from error

        logger.info(
            "twilio_verification_started user_id=%s phone_suffix=%s",
            user.public_id,
            payload.phone_number[-4:],
        )
        return PhoneVerificationStarted(
            phone_number=payload.phone_number,
            validation_code=str(validation.validation_code),
        )

    async def status(
        self,
        user: User,
        payload: PhoneVerificationRequest,
    ) -> PhoneVerificationStatus:
        client = self._client()
        try:
            caller_ids = await asyncio.to_thread(
                client.outgoing_caller_ids.list,
                phone_number=payload.phone_number,
                limit=1,
            )
        except TwilioRestException as error:
            logger.warning(
                "twilio_verification_status_failed user_id=%s twilio_code=%s",
                user.public_id,
                error.code,
            )
            raise PhoneVerificationError(
                "Twilio could not check this number yet."
            ) from error
        except Exception as error:
            logger.exception("twilio_verification_status_failed user_id=%s", user.public_id)
            raise PhoneVerificationError(
                "Twilio number verification is temporarily unavailable."
            ) from error

        verified = bool(caller_ids)
        return PhoneVerificationStatus(
            phone_number=payload.phone_number,
            verified=verified,
            status="verified" if verified else "pending",
        )


class VoiceCatalog:
    """Provider-aware capabilities for the voice configured by the operator."""

    def __init__(self, settings: AppSettings) -> None:
        self._voice = VoiceResponse(
            id=settings.cartesia_voice_id,
            name="Configured Cartesia voice",
            provider=settings.voice_provider,
            model=settings.voice_model,
            languages=sorted(SUPPORTED_VOICE_LANGUAGE_CODES),
            supports_speed=True,
        )

    def list(self) -> list[VoiceResponse]:
        return [self._voice]

    def validate(self, voice_id: str, language: str) -> VoiceResponse:
        if voice_id != self._voice.id:
            raise InvalidConfigurationError("voice is not available for the configured provider")
        if language.split("-", 1)[0].lower() not in self._voice.languages:
            raise InvalidConfigurationError(
                "language is not supported by the configured voice pipeline"
            )
        return self._voice


def settings_response(settings: AgentSettings) -> AgentSettingsResponse:
    return AgentSettingsResponse(
        provider=settings.provider,
        model=settings.model,
        voice_id=settings.voice_id,
        language=settings.language,
        personality=Personality(settings.personality),
        speaking_speed=settings.speaking_speed,
        custom_instructions=settings.custom_instructions,
        updated_at=settings.updated_at,
    )


def agent_response(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.public_id,
        name=agent.name,
        settings=settings_response(agent.settings),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def session_response(voice_session: VoiceSession) -> SessionResponse:
    return SessionResponse(
        id=voice_session.public_id,
        agent_id=voice_session.configuration_snapshot["agent_id"],
        room_name=voice_session.room_name,
        participant_identity=voice_session.participant_identity,
        status=SessionStatus(voice_session.status),
        configuration_snapshot=voice_session.configuration_snapshot,
        created_at=voice_session.created_at,
        started_at=voice_session.started_at,
        ended_at=voice_session.ended_at,
    )


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def create(self, display_name: str) -> UserCreatedResponse:
        token = create_api_token()
        user = User(
            public_id=f"usr_{uuid4().hex}",
            display_name=display_name,
            api_token_hash=hash_api_token(token),
        )
        await self.users.create(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info("user_created user_id=%s", user.public_id)
        return UserCreatedResponse(
            id=user.public_id,
            display_name=user.display_name,
            created_at=user.created_at,
            api_token=token,
        )

    @staticmethod
    def response(user: User) -> UserResponse:
        return UserResponse(
            id=user.public_id,
            display_name=user.display_name,
            created_at=user.created_at,
        )


class AgentService:
    def __init__(self, session: AsyncSession, app_settings: AppSettings) -> None:
        self.session = session
        self.agents = AgentRepository(session)
        self.catalog = VoiceCatalog(app_settings)
        self.app_settings = app_settings

    def _validated_settings(self, payload: AgentSettingsInput) -> AgentSettingsInput:
        voice_id = payload.voice_id or self.app_settings.cartesia_voice_id
        self.catalog.validate(voice_id, payload.language)
        return payload.model_copy(update={"voice_id": voice_id})

    async def create(self, user: User, payload: AgentCreate) -> AgentResponse:
        values = self._validated_settings(payload.settings)
        agent = Agent(public_id=f"agt_{uuid4().hex}", user_id=user.id, name=payload.name)
        agent_settings = AgentSettings(
            provider=self.app_settings.voice_provider,
            model=self.app_settings.voice_model,
            voice_id=values.voice_id,
            language=values.language,
            personality=values.personality.value,
            speaking_speed=values.speaking_speed,
            custom_instructions=values.custom_instructions,
        )
        await self.agents.create(agent, agent_settings)
        await self.session.commit()
        loaded = await self.agents.get_owned(user.id, agent.public_id)
        assert loaded is not None
        logger.info("agent_created user_id=%s agent_id=%s", user.public_id, agent.public_id)
        return agent_response(loaded)

    async def list(self, user: User) -> list[AgentResponse]:
        return [agent_response(item) for item in await self.agents.list_owned(user.id)]

    async def get_model(self, user: User, agent_id: str) -> Agent:
        agent = await self.agents.get_owned(user.id, agent_id)
        if agent is None:
            raise ResourceNotFoundError("agent not found")
        return agent

    async def get(self, user: User, agent_id: str) -> AgentResponse:
        return agent_response(await self.get_model(user, agent_id))

    async def update(self, user: User, agent_id: str, payload: AgentUpdate) -> AgentResponse:
        agent = await self.get_model(user, agent_id)
        agent.name = payload.name
        await self.session.commit()
        return agent_response(await self.get_model(user, agent_id))

    async def update_settings(
        self, user: User, agent_id: str, payload: AgentSettingsUpdate
    ) -> AgentSettingsResponse:
        agent = await self.get_model(user, agent_id)
        current = settings_response(agent.settings).model_dump(
            exclude={"provider", "model", "updated_at"}
        )
        current.update(payload.model_dump(exclude_none=True))
        values = self._validated_settings(AgentSettingsInput.model_validate(current))
        agent.settings.voice_id = values.voice_id or self.app_settings.cartesia_voice_id
        agent.settings.language = values.language
        agent.settings.personality = values.personality.value
        agent.settings.speaking_speed = values.speaking_speed
        agent.settings.custom_instructions = values.custom_instructions
        await self.session.commit()
        refreshed = await self.get_model(user, agent_id)
        logger.info("agent_updated user_id=%s agent_id=%s", user.public_id, agent_id)
        return settings_response(refreshed.settings)

    async def delete(self, user: User, agent_id: str) -> None:
        agent = await self.get_model(user, agent_id)
        await self.agents.delete(agent)
        await self.session.commit()
        logger.info("agent_deleted user_id=%s agent_id=%s", user.public_id, agent_id)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: datetime


class LiveKitTokenService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def issue(self, voice_session: VoiceSession, participant_name: str) -> IssuedToken:
        ttl = timedelta(seconds=self.settings.livekit_token_ttl_seconds)
        metadata = json.dumps(
            {
                "session_id": voice_session.public_id,
                "agent_id": voice_session.configuration_snapshot["agent_id"],
                "customer_name": participant_name,
                "direction": "web",
                "agent_config": voice_session.configuration_snapshot["settings"],
            },
            separators=(",", ":"),
        )
        token = (
            api.AccessToken(
                self.settings.livekit_api_key.get_secret_value(),
                self.settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(voice_session.participant_identity)
            .with_name(participant_name)
            .with_ttl(ttl)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=voice_session.room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_room_config(
                api.RoomConfiguration(
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=self.settings.livekit_agent_name,
                            metadata=metadata,
                        )
                    ]
                )
            )
            .to_jwt()
        )
        return IssuedToken(token=token, expires_at=datetime.now(UTC) + ttl)


class VoiceSessionService:
    def __init__(self, session: AsyncSession, settings: AppSettings) -> None:
        self.session = session
        self.sessions = VoiceSessionRepository(session)
        self.agent_service = AgentService(session, settings)
        self.tokens = LiveKitTokenService(settings)
        self.settings = settings

    def _configuration_snapshot(
        self,
        agent: Agent,
        temporary: AgentSettingsUpdate | None = None,
    ) -> dict[str, object]:
        saved = settings_response(agent.settings).model_dump(
            exclude={"provider", "model", "updated_at"}
        )
        if temporary is not None:
            saved.update(temporary.model_dump(exclude_none=True))
        runtime = self.agent_service._validated_settings(AgentSettingsInput.model_validate(saved))
        return {
            "agent_id": agent.public_id,
            "name": agent.name,
            "provider": self.settings.voice_provider,
            "model": self.settings.voice_model,
            "settings": {
                "agent_name": agent.name,
                "provider": self.settings.voice_provider,
                "model": self.settings.voice_model,
                "voice_id": runtime.voice_id,
                "language": runtime.language,
                "personality": runtime.personality.value,
                "speaking_speed": runtime.speaking_speed,
                "custom_instructions": runtime.custom_instructions,
            },
        }

    async def create(
        self,
        user: User,
        agent_id: str,
        temporary: AgentSettingsUpdate | None,
    ) -> SessionCreatedResponse:
        agent = await self.agent_service.get_model(user, agent_id)
        snapshot = self._configuration_snapshot(agent, temporary)
        session_id = f"ses_{uuid4().hex}"
        voice_session = VoiceSession(
            public_id=session_id,
            user_id=user.id,
            agent_id=agent.id,
            room_name=f"voice-{session_id}",
            participant_identity=f"user-{user.public_id[-12:]}-{uuid4().hex[:8]}",
            status=SessionStatus.CREATED.value,
            configuration_snapshot=snapshot,
        )
        await self.sessions.create(voice_session)
        try:
            issued = self.tokens.issue(voice_session, user.display_name)
        except Exception:
            await self.session.rollback()
            logger.exception("livekit_token_failed session_id=%s", session_id)
            raise
        await self.session.commit()
        base = session_response(voice_session)
        logger.info(
            "session_created user_id=%s agent_id=%s session_id=%s",
            user.public_id,
            agent_id,
            session_id,
        )
        return SessionCreatedResponse(
            **base.model_dump(),
            server_url=self.settings.livekit_url,
            participant_token=issued.token,
            token_expires_at=issued.expires_at,
        )

    async def create_outbound_call(
        self,
        user: User,
        payload: OutboundCallRequest,
    ) -> OutboundCallResponse:
        if self.settings.sip_outbound_trunk_id is None:
            raise InvalidConfigurationError("outbound SIP calling is not configured")

        if self.settings.twilio_trial_mode:
            verification = await PhoneVerificationService(self.settings).status(
                user,
                PhoneVerificationRequest(phone_number=payload.phone_number),
            )
            if not verification.verified:
                raise InvalidConfigurationError(
                    "This number is not verified for the Twilio trial account. "
                    "Add it under Verified Caller IDs in the Twilio Console, then try again."
                )

        agent = await self.agent_service.get_model(user, payload.agent_id)
        snapshot = self._configuration_snapshot(agent)
        session_id = f"ses_{uuid4().hex}"
        room_name = f"outbound-{session_id}"
        voice_session = VoiceSession(
            public_id=session_id,
            user_id=user.id,
            agent_id=agent.id,
            room_name=room_name,
            participant_identity=f"callee-{uuid4().hex[:16]}",
            status=SessionStatus.CONNECTING.value,
            configuration_snapshot=snapshot,
        )
        await self.sessions.create(voice_session)
        await self.session.commit()

        metadata = json.dumps(
            {
                "session_id": session_id,
                "agent_id": agent.public_id,
                "customer_name": payload.customer_name,
                "direction": "outbound",
                "phone_number": payload.phone_number,
                "agent_config": snapshot["settings"],
            },
            separators=(",", ":"),
        )
        try:
            async with api.LiveKitAPI(
                url=self.settings.livekit_url,
                api_key=self.settings.livekit_api_key.get_secret_value(),
                api_secret=self.settings.livekit_api_secret.get_secret_value(),
            ) as livekit_api:
                dispatch = await livekit_api.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name=self.settings.livekit_agent_name,
                        room=room_name,
                        metadata=metadata,
                    )
                )
        except Exception as error:
            voice_session.status = SessionStatus.FAILED.value
            voice_session.ended_at = datetime.now(UTC)
            await self.session.commit()
            logger.exception(
                "outbound_dispatch_failed user_id=%s session_id=%s",
                user.public_id,
                session_id,
            )
            raise OutboundDispatchError("LiveKit could not start the outbound call") from error

        logger.info(
            "outbound_dispatch_created user_id=%s agent_id=%s session_id=%s dispatch_id=%s",
            user.public_id,
            agent.public_id,
            session_id,
            dispatch.id,
        )
        return OutboundCallResponse(
            session_id=session_id,
            dispatch_id=dispatch.id,
            room_name=room_name,
            status=SessionStatus.CONNECTING,
        )

    async def get_model(self, user: User, session_id: str) -> VoiceSession:
        voice_session = await self.sessions.get_owned(user.id, session_id)
        if voice_session is None:
            raise ResourceNotFoundError("session not found")
        return voice_session

    async def get(self, user: User, session_id: str) -> SessionResponse:
        return session_response(await self.get_model(user, session_id))

    async def refresh_token(self, user: User, session_id: str) -> LiveKitTokenResponse:
        voice_session = await self.get_model(user, session_id)
        if voice_session.status in {SessionStatus.ENDED.value, SessionStatus.FAILED.value}:
            raise InvalidSessionStateError("cannot issue a token for an ended session")
        issued = self.tokens.issue(voice_session, user.display_name)
        logger.info("livekit_token_created session_id=%s", session_id)
        return LiveKitTokenResponse(
            session_id=session_id,
            room_name=voice_session.room_name,
            server_url=self.settings.livekit_url,
            participant_token=issued.token,
            expires_at=issued.expires_at,
        )

    async def end(self, user: User, session_id: str) -> SessionResponse:
        voice_session = await self.get_model(user, session_id)
        if voice_session.status not in {SessionStatus.ENDED.value, SessionStatus.FAILED.value}:
            voice_session.status = SessionStatus.ENDED.value
            voice_session.ended_at = datetime.now(UTC)
            await self.session.commit()
        return session_response(voice_session)

    async def update_runtime_status(self, session_id: str, status: SessionStatus) -> None:
        voice_session = await self.sessions.get_by_public_id(session_id)
        if voice_session is None:
            raise ResourceNotFoundError("session not found")
        voice_session.status = status.value
        now = datetime.now(UTC)
        if (
            status in {SessionStatus.CONNECTED, SessionStatus.ACTIVE}
            and voice_session.started_at is None
        ):
            voice_session.started_at = now
        if status in {SessionStatus.ENDED, SessionStatus.FAILED}:
            voice_session.ended_at = now
        await self.session.commit()
