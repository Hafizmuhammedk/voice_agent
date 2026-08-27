"""Thin HTTP routes delegating to application services."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import AppSettings
from ..db.models import User
from ..schemas import (
    AgentCreate,
    AgentResponse,
    AgentSettingsResponse,
    AgentSettingsUpdate,
    AgentUpdate,
    CreateSessionRequest,
    HealthResponse,
    LiveKitTokenRequest,
    LiveKitTokenResponse,
    OutboundCallRequest,
    OutboundCallResponse,
    PhoneVerificationPolicy,
    PhoneVerificationRequest,
    PhoneVerificationStarted,
    PhoneVerificationStatus,
    SessionCreatedResponse,
    SessionResponse,
    SessionStatusUpdate,
    UserCreate,
    UserCreatedResponse,
    UserResponse,
    VoiceResponse,
)
from ..services import (
    AgentService,
    PhoneVerificationService,
    UserService,
    VoiceCatalog,
    VoiceSessionService,
)
from .dependencies import bearer, get_app_settings, get_current_user, get_db

router = APIRouter()
api = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Cheap process-liveness check with no external calls."""
    return HealthResponse(status="ok", service="voice-agent-api")


@api.post(
    "/users",
    response_model=UserCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_db)):
    """Create a user and return a bearer token once."""
    return await UserService(session).create(payload.display_name)


@api.get("/users/me", response_model=UserResponse, tags=["users"])
async def get_me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserService.response(user)


@api.post(
    "/agents",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
)
async def create_agent(
    payload: AgentCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await AgentService(session, settings).create(user, payload)


@api.get("/agents", response_model=list[AgentResponse], tags=["agents"])
async def list_agents(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await AgentService(session, settings).list(user)


@api.get("/agents/{agent_id}", response_model=AgentResponse, tags=["agents"])
async def get_agent(
    agent_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await AgentService(session, settings).get(user, agent_id)


@api.patch("/agents/{agent_id}", response_model=AgentResponse, tags=["agents"])
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await AgentService(session, settings).update(user, agent_id, payload)


@api.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["agents"],
)
async def delete_agent(
    agent_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> Response:
    await AgentService(session, settings).delete(user, agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api.get(
    "/agents/{agent_id}/settings",
    response_model=AgentSettingsResponse,
    tags=["agent settings"],
)
async def get_agent_settings(
    agent_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    agent = await AgentService(session, settings).get_model(user, agent_id)
    from ..services import settings_response

    return settings_response(agent.settings)


@api.patch(
    "/agents/{agent_id}/settings",
    response_model=AgentSettingsResponse,
    tags=["agent settings"],
)
async def update_agent_settings(
    agent_id: str,
    payload: AgentSettingsUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await AgentService(session, settings).update_settings(user, agent_id, payload)


@api.get("/voices", response_model=list[VoiceResponse], tags=["voices"])
async def list_voices(
    _: User = Depends(get_current_user),
    settings: AppSettings = Depends(get_app_settings),
) -> list[VoiceResponse]:
    """Return only voices truly configured for the active provider."""
    return VoiceCatalog(settings).list()


@api.post(
    "/sessions",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sessions"],
)
async def create_session(
    payload: CreateSessionRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await VoiceSessionService(session, settings).create(
        user, payload.agent_id, payload.temporary_settings
    )


@api.post(
    "/outbound-calls",
    response_model=OutboundCallResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["calls"],
)
async def create_outbound_call(
    payload: OutboundCallRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> OutboundCallResponse:
    """Dispatch the configured voice agent to an E.164 phone number."""
    return await VoiceSessionService(session, settings).create_outbound_call(user, payload)


@api.get(
    "/phone-verifications/policy",
    response_model=PhoneVerificationPolicy,
    tags=["calls"],
)
async def get_phone_verification_policy(
    _: User = Depends(get_current_user),
    settings: AppSettings = Depends(get_app_settings),
) -> PhoneVerificationPolicy:
    """Tell the client whether trial-recipient verification is available and required."""
    available = (
        settings.twilio_account_sid is not None
        and settings.twilio_auth_token is not None
    )
    return PhoneVerificationPolicy(
        available=available and not settings.twilio_trial_mode,
        required=False,
        manual_verification_required=settings.twilio_trial_mode,
    )


@api.post(
    "/phone-verifications",
    response_model=PhoneVerificationStarted,
    status_code=status.HTTP_201_CREATED,
    tags=["calls"],
)
async def start_phone_verification(
    payload: PhoneVerificationRequest,
    user: User = Depends(get_current_user),
    settings: AppSettings = Depends(get_app_settings),
) -> PhoneVerificationStarted:
    """Call a user with the Twilio validation code for a trial recipient."""
    return await PhoneVerificationService(settings).start(user, payload)


@api.post(
    "/phone-verifications/status",
    response_model=PhoneVerificationStatus,
    tags=["calls"],
)
async def get_phone_verification_status(
    payload: PhoneVerificationRequest,
    user: User = Depends(get_current_user),
    settings: AppSettings = Depends(get_app_settings),
) -> PhoneVerificationStatus:
    """Check whether Twilio accepted the validation code for a number."""
    return await PhoneVerificationService(settings).status(user, payload)


@api.get("/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await VoiceSessionService(session, settings).get(user, session_id)


@api.post("/sessions/{session_id}/end", response_model=SessionResponse, tags=["sessions"])
async def end_session(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    return await VoiceSessionService(session, settings).end(user, session_id)


@api.post("/livekit/token", response_model=LiveKitTokenResponse, tags=["livekit"])
async def create_livekit_token(
    payload: LiveKitTokenRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
):
    """Issue a short-lived room token without exposing the LiveKit API secret."""
    return await VoiceSessionService(session, settings).refresh_token(user, payload.session_id)


@api.patch(
    "/internal/sessions/{session_id}/status",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def update_runtime_session_status(
    session_id: str,
    payload: SessionStatusUpdate,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> Response:
    expected = settings.backend_api_token
    if expected is None:
        raise HTTPException(status_code=503, detail="runtime callback is not configured")
    supplied = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(supplied, expected.get_secret_value()):
        raise HTTPException(status_code=401, detail="invalid runtime credential")
    await VoiceSessionService(session, settings).update_runtime_status(session_id, payload.status)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(api)
