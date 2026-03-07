from fastapi import FastAPI

from app.config import Config
from app.schema import UserCreate, UserRead, UserUpdate

from .dependencies import fastapi_users
from .factories import UserManager, user_manager_factory
from .require_user import require_user


def register_auth_views(app: FastAPI) -> None:
    from .dependencies import cookie_auth_backend, jwt_auth_backend

    # /auth/v1/jwt/login (POST multipart/form-data with fields username="email", password="pw")
    # /auth/v1/jwt/logout (authed POST)
    # Returns bearer token in response body
    app.include_router(
        fastapi_users.get_auth_router(
            jwt_auth_backend, requires_verification=Config.AUTH_REQUIRE_USER_VERIFIED
        ),
        prefix=f"{Config.AUTH_URL_PREFIX}/jwt",
        tags=["auth"],
    )

    # /auth/v1/cookie/login (POST multipart/form-data with fields username="email", password="pw")
    # /auth/v1/cookie/logout (authed POST)
    # Sets httpOnly cookie
    app.include_router(
        fastapi_users.get_auth_router(
            cookie_auth_backend,
            requires_verification=Config.AUTH_REQUIRE_USER_VERIFIED,
        ),
        prefix=f"{Config.AUTH_URL_PREFIX}/cookie",
        tags=["auth"],
    )

    # /auth/v1/register (POST application/json UserCreate)
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix=Config.AUTH_URL_PREFIX,
        tags=["auth"],
    )

    # /auth/v1/forgot-password (POST application/json email="email")
    # /auth/v1/reset-password (POST application/json token="forgot-pw-token", password="new-pw")
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix=Config.AUTH_URL_PREFIX,
        tags=["auth"],
    )

    # /auth/v1/request-verify-token (POST application/json email="email")
    # /auth/v1/verify (POST application/json token="verify-token", email="email")
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix=Config.AUTH_URL_PREFIX,
        tags=["auth"],
    )

    # /auth/v1/users/me (authed GET/PATCH)
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix=f"{Config.AUTH_URL_PREFIX}/users",
        tags=["users"],
    )


__all__ = [
    "UserCreate",
    "UserRead",
    "UserUpdate",
    "UserManager",
    "fastapi_users",
    "require_user",
    "user_manager_factory",
]
