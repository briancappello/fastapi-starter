from fastapi import BackgroundTasks
from fastapi_users.authentication.strategy import DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from app.db import models

from .user_manager import UserManager


def user_db_factory(
    session: AsyncSession,
) -> SQLAlchemyUserDatabase[models.User, int]:
    return SQLAlchemyUserDatabase(session, models.User)


def access_token_db_factory(
    session: AsyncSession,
) -> SQLAlchemyAccessTokenDatabase[models.AccessToken]:
    return SQLAlchemyAccessTokenDatabase(session, models.AccessToken)


def user_manager_factory(
    session: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
    send_emails: bool = True,
) -> UserManager:
    return UserManager(
        user_db=user_db_factory(session),
        background_tasks=background_tasks,
        send_emails=send_emails,
    )


def db_strategy_factory(
    session: AsyncSession,
) -> DatabaseStrategy[models.User, int, models.AccessToken]:
    return DatabaseStrategy(
        access_token_db_factory(session),
        lifetime_seconds=Config.AUTH_TOKEN_LIFETIME_SECONDS,
    )
