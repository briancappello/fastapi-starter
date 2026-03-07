"""Custom test client with authentication helpers."""

from __future__ import annotations

import secrets

from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import User


class TestClient(AsyncClient):
    """
    AsyncClient subclass with authentication helpers.

    Usage:
        # Unauthenticated request
        response = await client.get("/public/endpoint")

        # Authenticated request
        response = await client.with_user(user).get("/protected/endpoint")

        # Admin request
        response = await client.with_user(admin).post("/admin/endpoint", json={...})
    """

    def __init__(self, *args, session: "AsyncSession", **kwargs):
        super().__init__(*args, **kwargs)
        self._session = session
        self._auth_user: "User | None" = None
        self._auth_token: str | None = None

    def with_user(self, user: "User") -> "TestClient":
        """
        Return a client authenticated as the given user.

        Creates an access token for the user and sets the Authorization header.
        The token is created lazily on first request.

        Args:
            user: The user to authenticate as

        Returns:
            Self for method chaining
        """
        self._auth_user = user
        self._auth_token = None  # Will be created on first request
        return self

    async def _ensure_auth_token(self) -> None:
        """Create access token for the authenticated user if needed."""
        if self._auth_user is None:
            # Clear any existing auth header
            self.headers.pop("Authorization", None)
            return

        if self._auth_token is None:
            from app.db.models import AccessToken

            # Generate a unique token
            self._auth_token = secrets.token_urlsafe(32)
            access_token = AccessToken(
                token=self._auth_token,
                user_id=self._auth_user.id,
            )
            self._session.add(access_token)
            await self._session.flush()

        self.headers["Authorization"] = f"Bearer {self._auth_token}"

    async def request(self, *args, **kwargs):
        """Override request to ensure auth token is set."""
        await self._ensure_auth_token()
        return await super().request(*args, **kwargs)


async def create_test_client(
    app,
    session: "AsyncSession",
    base_url: str = "https://test",
) -> TestClient:
    """
    Create a TestClient instance.

    Args:
        app: The FastAPI application
        session: The database session to use for creating tokens
        base_url: Base URL for requests (default: https://test)

    Returns:
        Configured TestClient instance
    """
    transport = ASGITransport(app=app)
    return TestClient(
        transport=transport,
        base_url=base_url,
        session=session,
    )
