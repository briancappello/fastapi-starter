from typing import AsyncGenerator

from fastapi import BackgroundTasks, Depends
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
)
from fastapi_users.authentication.strategy import DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from app.db import get_async_session, models

from .factories import (
    access_token_db_factory,
    db_strategy_factory,
    user_db_factory,
    user_manager_factory,
)
from .user_manager import UserManager


# for DI
async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase[models.User, int], None]:
    yield user_db_factory(session)


# for DI
async def get_access_token_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyAccessTokenDatabase[models.AccessToken], None]:
    yield access_token_db_factory(session)


# for DI
async def get_user_manager(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[UserManager, None]:
    yield user_manager_factory(session, background_tasks=background_tasks)


# for DI
async def get_db_strategy(
    session: AsyncSession = Depends(get_async_session),
) -> DatabaseStrategy[models.User, int, models.AccessToken]:
    return db_strategy_factory(session)


jwt_bearer_transport = BearerTransport(
    tokenUrl=f"{Config.AUTH_URL_PREFIX.lstrip('/')}/login"
)
jwt_auth_backend = AuthenticationBackend(
    name="jwt",
    transport=jwt_bearer_transport,
    get_strategy=get_db_strategy,
)
fastapi_users = FastAPIUsers[models.User, int](
    get_user_manager,
    auth_backends=[jwt_auth_backend],
)
