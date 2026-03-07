"""Shared pytest fixtures for all tests."""

from typing import AsyncGenerator, Generator

import pytest

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app import app
from app.auth import UserManager, user_manager_factory
from app.config import Config
from app.db import async_session
from app.db.models import Base, User
from app.schema import UserCreate

from .client import TestClient


# Set a proper length secret key to avoid InsecureKeyLengthWarning from PyJWT.
# Must be at least 32 bytes for SHA256.
_TEST_SECRET_KEY = "test-secret-key-that-is-at-least-32-bytes-long!"
Config.SECRET_KEY = _TEST_SECRET_KEY
UserManager.reset_password_token_secret = _TEST_SECRET_KEY
UserManager.verification_token_secret = _TEST_SECRET_KEY


@pytest.fixture(autouse=True)
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_connection_string() -> str:
    db_url, db_name = Config.SQL_DB_URL.rsplit("/", maxsplit=1)
    return f"{db_url}/{db_name}_test"


@pytest.fixture
def db_schema_name(request: pytest.FixtureRequest) -> str | None:
    schema_name = None
    if (
        xdist := request.config.pluginmanager.get_plugin("xdist")
    ) and xdist.is_xdist_worker(request):
        class_name = request.cls and request.cls.__name__.lower() or ""
        test_fn_name = request.function and request.function.__name__ or "public"
        schema_name = class_name and f"{class_name}_{test_fn_name}" or test_fn_name

        # max schema name length for postgresql
        if len(schema_name) > 63:
            return schema_name[-63:]  # trim off beginning of string

    return schema_name


@pytest.fixture
def engine(db_connection_string: str) -> Generator[AsyncEngine, None, None]:
    eng = create_async_engine(
        db_connection_string,
        future=True,
        pool_size=20,
        max_overflow=2,
    )
    yield eng


@pytest.fixture
async def session(
    engine: AsyncEngine, db_schema_name: str | None
) -> AsyncGenerator[AsyncSession, None]:
    execution_options = {}
    if db_schema_name not in {None, "public"}:
        execution_options["schema_translate_map"] = {None: db_schema_name}

    async_engine = engine.execution_options(**execution_options)
    connection = await async_engine.connect()
    transaction = await connection.begin()

    if db_schema_name not in {None, "public"}:
        await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {db_schema_name}"))
    await connection.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async_session = session_factory(bind=connection)
    nested = await connection.begin_nested()

    @event.listens_for(async_session.sync_session, "after_transaction_end")
    def end_savepoint(sess, trans):
        nonlocal nested

        if not nested.is_active:
            nested = connection.sync_connection.begin_nested()

    yield async_session

    await transaction.rollback()
    await connection.run_sync(Base.metadata.drop_all)
    await async_session.close()
    await connection.close()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    """
    Test client with authentication helpers.

    Usage:
        # Unauthenticated request
        response = await client.get("/public/endpoint")

        # Authenticated as regular user
        response = await client.with_user(user).get("/protected/endpoint")

        # Authenticated as admin
        response = await client.with_user(admin).get("/admin/endpoint")
    """
    from httpx import ASGITransport

    app.dependency_overrides[async_session] = lambda: session

    # Use https:// so cookies with Secure flag work correctly
    test_client = TestClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        session=session,
    )

    async with test_client:
        yield test_client

    del app.dependency_overrides[async_session]


@pytest.fixture
def user_manager(session: AsyncSession) -> UserManager:
    """User manager for creating test users."""
    return user_manager_factory(session, send_emails=False)


@pytest.fixture
async def user(user_manager) -> User:
    """A regular verified user."""
    return await user_manager.create(
        UserCreate(
            email="test@example.com",
            password="password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            first_name="Test",
            last_name="User",
        )
    )


@pytest.fixture
async def superuser(user_manager) -> User:
    """Alias for admin fixture (backward compatibility)."""
    return await user_manager.create(
        UserCreate(
            email="admin@example.com",
            password="password",
            is_active=True,
            is_verified=True,
            is_superuser=True,
            first_name="Admin",
            last_name="User",
        )
    )


@pytest.fixture
async def authed_client(client: TestClient, user: User) -> TestClient:
    """
    Client authenticated as regular user (backward compatibility).

    Prefer using: client.with_user(user)
    """
    return client.with_user(user)


@pytest.fixture
async def authed_superuser_client(client: TestClient, admin: User) -> TestClient:
    """
    Client authenticated as admin (backward compatibility).

    Prefer using: client.with_user(admin)
    """
    return client.with_user(admin)
