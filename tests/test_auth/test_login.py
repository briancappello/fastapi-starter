"""Tests for login/logout flows (JWT and Cookie)."""

import pytest

from httpx import AsyncClient

from app.config import Config


class TestJWTLogin:
    """Tests for POST /auth/v1/jwt/login and /auth/v1/jwt/logout"""

    async def test_jwt_login_success(self, client: AsyncClient, user):
        """Test successful JWT login."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": user.email,
                "password": "password",  # From conftest user fixture
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_jwt_login_wrong_password(self, client: AsyncClient, user):
        """Test JWT login with wrong password fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": user.email,
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 400
        assert "LOGIN_BAD_CREDENTIALS" in response.json()["detail"]

    async def test_jwt_login_nonexistent_user(self, client: AsyncClient):
        """Test JWT login with non-existent user fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": "nobody@example.com",
                "password": "password",
            },
        )
        assert response.status_code == 400
        assert "LOGIN_BAD_CREDENTIALS" in response.json()["detail"]

    async def test_jwt_login_unverified_user_fails(
        self, client: AsyncClient, user_manager, captured_emails
    ):
        """Test JWT login with unverified user fails when verification required."""
        from app.schema import UserCreate

        # Create an unverified user
        unverified = await user_manager.create(
            UserCreate(
                email="unverified@example.com",
                password="password",
                first_name="Unverified",
                last_name="User",
                is_verified=False,
            )
        )

        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": unverified.email,
                "password": "password",
            },
        )
        # When AUTH_REQUIRE_USER_VERIFIED is True, unverified users can't login
        if Config.AUTH_REQUIRE_USER_VERIFIED:
            assert response.status_code == 400
            assert "LOGIN_USER_NOT_VERIFIED" in response.json()["detail"]
        else:
            assert response.status_code == 200

    async def test_jwt_login_inactive_user_fails(
        self, client: AsyncClient, user_manager, captured_emails
    ):
        """Test JWT login with inactive user fails."""
        from app.schema import UserCreate

        # Create an inactive user
        inactive = await user_manager.create(
            UserCreate(
                email="inactive@example.com",
                password="password",
                first_name="Inactive",
                last_name="User",
                is_active=False,
                is_verified=True,
            )
        )

        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": inactive.email,
                "password": "password",
            },
        )
        assert response.status_code == 400
        assert "LOGIN_BAD_CREDENTIALS" in response.json()["detail"]

    async def test_jwt_logout(self, client: AsyncClient, authed_client):
        """Test JWT logout."""
        response = await authed_client.post(f"{Config.AUTH_URL_PREFIX}/jwt/logout")
        # fastapi-users returns 200 or 204 on logout
        assert response.status_code in (200, 204)

    async def test_jwt_logout_unauthenticated(self, client: AsyncClient):
        """Test JWT logout without authentication fails."""
        response = await client.post(f"{Config.AUTH_URL_PREFIX}/jwt/logout")
        assert response.status_code == 401


class TestCookieLogin:
    """Tests for POST /auth/v1/cookie/login and /auth/v1/cookie/logout"""

    async def test_cookie_login_success(self, client: AsyncClient, user):
        """Test successful cookie login."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/cookie/login",
            data={
                "username": user.email,
                "password": "password",
            },
        )
        # Cookie login returns 200 or 204 depending on fastapi-users version
        assert response.status_code in (200, 204)
        # Should set a cookie
        assert "fastapiusersauth" in response.cookies or any(
            "auth" in c.lower() for c in response.cookies.keys()
        )

    async def test_cookie_login_wrong_password(self, client: AsyncClient, user):
        """Test cookie login with wrong password fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/cookie/login",
            data={
                "username": user.email,
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 400

    async def test_cookie_logout(self, client: AsyncClient, user):
        """Test cookie logout."""
        # First login to get a cookie
        login_response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/cookie/login",
            data={
                "username": user.email,
                "password": "password",
            },
        )
        assert login_response.status_code in (200, 204)

        # Set the auth cookie on the client for subsequent requests
        client.cookies.update(login_response.cookies)

        # Now logout
        response = await client.post(f"{Config.AUTH_URL_PREFIX}/cookie/logout")
        assert response.status_code in (200, 204)
