"""Isolated AI PostgreSQL adapter, independent of RetailOps operational data."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from retailops_ai.config import Settings

EXPECTED_REVISION = "0020_intelligence_outbox"


def database_engine(settings: Settings) -> AsyncEngine:
    if settings.database_url is None:
        raise ValueError("AI database URL is required")
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=2,
        connect_args={"connect_timeout": 1},
    )


class DatabaseProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def check(self) -> bool:
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM ai.alembic_version"))
            if revision != EXPECTED_REVISION:
                return False
            if (
                await connection.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
                != 1
            ):
                return False
            await connection.execute(text("SELECT 1 FROM ai.service_metadata LIMIT 1"))
        return True
