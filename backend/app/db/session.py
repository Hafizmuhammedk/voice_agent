"""Async database engine and request-scoped session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        if url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", self._enable_sqlite_foreign_keys)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _enable_sqlite_foreign_keys(connection: object, _: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if str(self.engine.url).startswith("sqlite"):
                columns = await connection.run_sync(
                    lambda sync_connection: {
                        column["name"]
                        for column in inspect(sync_connection).get_columns("agent_settings")
                    }
                )
                if "company_name" not in columns:
                    await connection.exec_driver_sql(
                        "ALTER TABLE agent_settings ADD COLUMN company_name "
                        "VARCHAR(120) NOT NULL DEFAULT 'Your hotel'"
                    )

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session
