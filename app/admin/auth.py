from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.router.common import ErrorCode
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.auth import fastapi_users, user_manager_factory
from app.auth.factories import db_strategy_factory
from app.config import Config
from app.db import async_session_factory
from app.db.models import User


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        async with async_session_factory() as session:
            user_manger = user_manager_factory(session)
            user = await user_manger.authenticate(
                OAuth2PasswordRequestForm(
                    username=form["username"],
                    password=form["password"],
                )
            )

            if user is None:
                return False

            if not all([user.is_active, user.is_superuser]):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="forbidden",
                )

            if Config.AUTH_REQUIRE_USER_VERIFIED and not user.is_verified:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.LOGIN_USER_NOT_VERIFIED,
                )

            db_strategy = db_strategy_factory(session)
            token = await db_strategy.write_token(user)
            request.session.update({"token": token})
            return True

    async def authenticate(self, request: Request) -> bool:
        user, token = await self._authenticate_request(request)
        return bool(user) and bool(token)

    async def logout(self, request: Request) -> bool:
        await self._authenticate_request(request, logout=True)
        request.session.clear()
        return True

    async def _authenticate_request(
        self,
        request: Request,
        logout: bool = False,
    ) -> tuple[User | None, str | None]:
        """
        Validate request token, optionally logging out the user
        """
        token = request.session.get("token")
        if not token:
            return None, None

        async with async_session_factory() as session:
            db_strategy = db_strategy_factory(session)
            user, token = await fastapi_users.authenticator._authenticate(
                user_manager=user_manager_factory(session),
                strategy_jwt=db_strategy,
                jwt=token,
                active=True,
                verified=Config.AUTH_REQUIRE_USER_VERIFIED,
                superuser=True,
            )

            if logout:
                await db_strategy.destroy_token(token, user)
                return user, None

            return user, token
