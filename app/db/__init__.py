from contextlib import asynccontextmanager
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
    # Connection pool settings — tunable via DB_POOL_SIZE / DB_POOL_MAX_OVERFLOW env vars
    pool_size=Config.DB_POOL_SIZE,
    max_overflow=Config.DB_POOL_MAX_OVERFLOW,
    pool_timeout=30,  # Seconds to wait for a connection before raising an error
    pool_recycle=1800,  # Recycle connections after 30 minutes to avoid stale connections
    # (important for PostgreSQL which can drop idle connections)
    pool_pre_ping=True,  # Tests before use to handle dropped connections gracefully
    # Enable echo for debugging (set to False in production)
    echo=False,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# for DI
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


def update_pool_metrics() -> None:
    """Snapshot the DB connection pool state into Prometheus gauges.

    Called from the ``/metrics`` endpoint so values are fresh on each
    scrape.  The pool object is synchronous (no await needed).
    """
    from app.metrics import (
        DB_POOL_CHECKED_IN,
        DB_POOL_CHECKED_OUT,
        DB_POOL_OVERFLOW,
        DB_POOL_SIZE,
    )

    pool = async_engine.sync_engine.pool
    DB_POOL_SIZE.set(pool.size())
    DB_POOL_CHECKED_OUT.set(pool.checkedout())
    DB_POOL_OVERFLOW.set(pool.overflow())
    DB_POOL_CHECKED_IN.set(pool.checkedin())


__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "async_engine",
    "async_session_factory",
    "async_session",
    "select",
    "selectinload",
    "update_pool_metrics",
]
