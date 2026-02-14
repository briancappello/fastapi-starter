from typing import AsyncGenerator, Generator

import pytest

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app import app
from app.auth import UserManager, user_manager_factory
from app.config import Config
from app.db import async_session_factory, get_async_session
from app.db.models import AccessToken, Base
from app.schema import UserCreate


@pytest.fixture(autouse=True)
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_async_session] = lambda: session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    del app.dependency_overrides[get_async_session]


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
    yield create_async_engine(
        db_connection_string,
        future=True,
        pool_size=20,
        max_overflow=2,
    )


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

    async_session_factory.configure(bind=connection)
    yield async_session

    await transaction.rollback()
    await connection.run_sync(Base.metadata.drop_all)
    await async_session.close()
    await connection.close()


@pytest.fixture
def user_manager(session: AsyncSession) -> UserManager:
    return user_manager_factory(session, send_emails=False)


@pytest.fixture
async def user(user_manager):
    user_create = UserCreate(
        email="test@example.com",
        password="password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        first_name="Test",
        last_name="User",
    )
    return await user_manager.create(user_create)


@pytest.fixture
async def superuser(user_manager):
    user_create = UserCreate(
        email="admin@example.com",
        password="password",
        is_active=True,
        is_verified=True,
        is_superuser=True,
        first_name="Admin",
        last_name="User",
    )
    return await user_manager.create(user_create)


@pytest.fixture
async def authed_client(client, user, session):
    token = "test_token"
    access_token = AccessToken(token=token, user_id=user.id)
    session.add(access_token)
    await session.commit()

    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
async def authed_superuser_client(client, superuser, session):
    token = "admin_token"
    access_token = AccessToken(token=token, user_id=superuser.id)
    session.add(access_token)
    await session.commit()

    client.headers["Authorization"] = f"Bearer {token}"
    return client
