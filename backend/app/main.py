"""FastAPI application factory and resource lifecycle."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .core.config import AppSettings, get_settings
from .db.session import Database
from .services import (
    InvalidConfigurationError,
    InvalidSessionStateError,
    OutboundDispatchError,
    PhoneVerificationError,
    PhoneVerificationRateLimitError,
    ResourceNotFoundError,
)

logger = logging.getLogger("voice-agent.backend")


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    database = Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="1.0.0",
        description="Configuration, authentication, sessions, and LiveKit access for a full-duplex voice agent.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", f"req_{uuid4().hex}")[:80]
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed request_id=%s path=%s", request_id, request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                }
            )
        )
        return response

    @app.exception_handler(ResourceNotFoundError)
    async def not_found(_: Request, error: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(InvalidConfigurationError)
    async def invalid_config(_: Request, error: InvalidConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(InvalidSessionStateError)
    async def invalid_state(_: Request, error: InvalidSessionStateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(OutboundDispatchError)
    async def outbound_dispatch_failed(_: Request, error: OutboundDispatchError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.exception_handler(PhoneVerificationError)
    async def phone_verification_failed(_: Request, error: PhoneVerificationError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.exception_handler(PhoneVerificationRateLimitError)
    async def phone_verification_limited(
        _: Request, error: PhoneVerificationRateLimitError
    ) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(error)})

    app.include_router(router)
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="voice-client")
    return app
