"""Tests for admin access token operations."""

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessToken, User


@pytest.fixture
async def user_with_token(user_manager, session: AsyncSession) -> tuple[User, str]:
    """Create a user and generate an access token for them."""
    from app.auth.factories import db_strategy_factory
    from app.schema import UserCreate

    # Create a user
    user = await user_manager.create(
        UserCreate(
            email="tokenuser@example.com",
            password="password",
            first_name="Token",
            last_name="User",
            is_active=True,
            is_verified=True,
        )
    )

    # Create an access token using the db strategy
    db_strategy = db_strategy_factory(session)
    token = await db_strategy.write_token(user)

    return user, token


class TestAccessTokenList:
    """Tests for access token list view."""

    async def test_list_access_tokens(self, admin_client):
        """Test that access token list page loads."""
        response = await admin_client.get("/admin/access-token/list")
        assert response.status_code == 200

    async def test_list_shows_tokens(
        self, admin_client, user_with_token: tuple[User, str]
    ):
        """Test that tokens are visible in the list."""
        user, token = user_with_token
        response = await admin_client.get("/admin/access-token/list")
        assert response.status_code == 200
        # The token or part of it should be visible
        # (SQLAdmin may truncate long strings in list view)
        assert token[:10] in response.text or user.email in response.text

    async def test_list_shows_user_association(
        self, admin_client, user_with_token: tuple[User, str]
    ):
        """Test that token list shows associated user."""
        user, token = user_with_token
        response = await admin_client.with_user(user).get("/admin/access-token/list")
        assert response.status_code == 200
        # Should show user info (email or ID)
        html = response.text
        assert str(user.id) in html or user.email in html


class TestAccessTokenDetail:
    """Tests for access token detail view."""

    async def test_view_token_details(
        self, admin_client, user_with_token: tuple[User, str]
    ):
        """Test viewing token details."""
        user, token = user_with_token
        response = await admin_client.with_user(user).get(
            f"/admin/access-token/details/{token}"
        )
        assert response.status_code == 200
        assert token in response.text

    async def test_view_nonexistent_token(self, admin_client):
        """Test viewing non-existent token returns 404."""
        response = await admin_client.get(
            "/admin/access-token/details/nonexistent-token-12345"
        )
        assert response.status_code == 404


class TestAccessTokenDelete:
    """Tests for access token deletion."""

    async def test_delete_token(
        self,
        admin_client,
        user_with_token: tuple[User, str],
        session: AsyncSession,
    ):
        """Test deleting an access token."""
        user, token = user_with_token

        # Verify token exists
        from sqlalchemy import select

        result = await session.execute(
            select(AccessToken).where(AccessToken.token == token)
        )
        assert result.scalar_one_or_none() is not None

        # SQLAdmin uses DELETE method with query params
        response = await admin_client.delete(
            f"/admin/access-token/delete?pks={token}",
            follow_redirects=False,
        )
        assert response.status_code == 200

        # Verify token was deleted
        session.expire_all()
        result = await session.execute(
            select(AccessToken).where(AccessToken.token == token)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_token(self, admin_client):
        """Test deleting non-existent token returns 404."""
        response = await admin_client.delete(
            "/admin/access-token/delete?pks=nonexistent-token-12345",
            follow_redirects=False,
        )
        assert response.status_code == 404

    async def test_delete_invalidates_auth(
        self,
        client,
        admin_client,
        user_with_token: tuple[User, str],
        session: AsyncSession,
    ):
        """Test that deleting a token invalidates authentication."""
        user, token = user_with_token

        # First verify the token works for auth
        # Use the token for a JWT-protected endpoint
        auth_response = await client.with_user(user).get(
            "/auth/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_response.status_code == 200

        # Delete the token via admin (DELETE method with query params)
        await admin_client.delete(
            f"/admin/access-token/delete?pks={token}",
            follow_redirects=False,
        )

        # Clear session cache
        session.expire_all()

        # Token should no longer work
        auth_response = await client.get(
            "/auth/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_response.status_code == 401


class TestAccessTokenNoCreate:
    """Test that access tokens cannot be created via admin."""

    async def test_create_not_available(self, admin_client):
        """Test that create endpoint doesn't exist or is disabled for tokens."""
        response = await admin_client.get("/admin/access-token/create")
        assert response.status_code == 403


class TestAccessTokenEdit:
    """Test that access tokens cannot be edited via admin."""

    async def test_edit_not_available(
        self, admin_client, user_with_token: tuple[User, str]
    ):
        """Test that edit endpoint doesn't exist or is disabled for tokens."""
        user, token = user_with_token
        response = await admin_client.get(f"/admin/access-token/edit/{token}")
        assert response.status_code == 403
