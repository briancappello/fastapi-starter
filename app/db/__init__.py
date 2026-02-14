from typing import AsyncGenerator, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app.config import Config

from . import models


async_engine: AsyncEngine = create_async_engine(
    Config.SQL_DB_URL,
    # Connection pool settings
    pool_size=5,  # Base number of persistent connections (default is 5)
    max_overflow=10,  # Additional connections allowed above pool_size during high load
    pool_timeout=30,  # Seconds to wait for a connection before raising an error
    pool_recycle=1800,  # Recycle connections after 30 minutes to avoid stale connections
    # (important for PostgreSQL which can drop idle connections)
    pool_pre_ping=True,  # Tests before use to handle dropped connections gracefully
    # Enable echo for debugging (set to False in production)
    echo=False,
)

async_session_factory: Callable[[], AsyncSession] = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# for DI
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "async_engine",
    "async_session_factory",
    "get_async_session",
    "select",
    "selectinload",
]
