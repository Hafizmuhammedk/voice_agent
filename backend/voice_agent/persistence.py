"""Optional non-blocking persistence client used by the voice runtime."""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger("voice-agent")


class BackendClient:
    """Small optional REST client; disabled when no backend URL is configured."""

    def __init__(self) -> None:
        base_url = os.getenv("BACKEND_API_URL") or os.getenv("API_BASE_URL")
        self._base_url = base_url.rstrip("/") if base_url else None
        self._token = os.getenv("BACKEND_API_TOKEN")
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return self._base_url is not None

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        warn: bool = True,
    ) -> bool:
        if self._base_url is None:
            return False

        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            )

        try:
            async with self._session.post(
                f"{self._base_url}/{path.lstrip('/')}",
                json=payload or {},
            ) as response:
                await response.read()
                if 200 <= response.status < 300:
                    return True
                if warn:
                    logger.warning(
                        "Backend request failed: path=%s status=%s",
                        path,
                        response.status,
                    )
        except (TimeoutError, aiohttp.ClientError):
            if warn:
                logger.warning("Backend request failed: path=%s", path, exc_info=True)
        return False

    async def patch_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        warn: bool = True,
    ) -> bool:
        if self._base_url is None:
            return False

        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            )

        try:
            async with self._session.patch(
                f"{self._base_url}/{path.lstrip('/')}",
                json=payload or {},
            ) as response:
                await response.read()
                if 200 <= response.status < 300:
                    return True
                if warn:
                    logger.warning(
                        "Backend request failed: path=%s status=%s",
                        path,
                        response.status,
                    )
        except (TimeoutError, aiohttp.ClientError):
            if warn:
                logger.warning("Backend request failed: path=%s", path, exc_info=True)
        return False

    async def update_session_status(self, session_id: str, status: str) -> bool:
        return await self.patch_json(
            f"/api/v1/internal/sessions/{session_id}/status",
            {"status": status},
        )

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
