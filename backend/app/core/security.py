"""Opaque bearer-token authentication with hashed token storage."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User
from ..repositories import UserRepository


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_token() -> str:
    return f"vua_{secrets.token_urlsafe(32)}"


class AuthenticationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def authenticate(self, token: str) -> User | None:
        if not token.startswith("vua_") or len(token) > 128:
            return None
        return await self.users.get_by_token_hash(hash_api_token(token))
