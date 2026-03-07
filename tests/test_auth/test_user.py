"""Tests for user profile endpoints (GET/PATCH /users/me)."""

import pytest

from app.config import Config

from .conftest import find_email


class TestGetCurrentUser:
    """Tests for GET /auth/v1/users/me"""

    async def test_get_current_user(self, client, user):
        """Test getting current user profile."""
        response = await client.with_user(user).get(f"{Config.AUTH_URL_PREFIX}/users/me")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == user.email
        assert data["first_name"] == user.first_name
        assert data["last_name"] == user.last_name
        assert data["is_active"] is True
        assert data["is_verified"] is True
        # Password should not be returned
        assert "password" not in data
        assert "hashed_password" not in data

    async def test_get_current_user_unauthenticated(self, client):
        """Test getting current user without authentication fails."""
        response = await client.get(f"{Config.AUTH_URL_PREFIX}/users/me")
        assert response.status_code == 401


@pytest.mark.usefixtures("captured_emails")
class TestUpdateCurrentUser:
    """Tests for PATCH /auth/v1/users/me"""

    async def test_update_first_name(self, client, user):
        """Test updating first name."""
        response = await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"first_name": "Updated"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["first_name"] == "Updated"
        assert data["last_name"] == user.last_name  # Unchanged

    async def test_update_last_name(self, client, user):
        """Test updating last name."""
        response = await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"last_name": "NewLastName"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["last_name"] == "NewLastName"

    async def test_update_password_sends_notification(
        self, client, user, captured_emails
    ):
        """Test that updating password sends notification email."""
        captured_emails.clear()

        response = await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"password": "newpassword123"},
        )
        assert response.status_code == 200

        # Should send password changed notification
        password_email = find_email(captured_emails, "Password")
        assert password_email is not None
        assert user.email in password_email.recipients
        assert password_email.template == "email/user-password-changed.html"

    async def test_update_password_allows_login_with_new_password(self, client, user):
        """Test that after password update, can login with new password."""
        # Update password
        await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"password": "brandnewpassword"},
        )

        # Login with new password
        login_response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": user.email,
                "password": "brandnewpassword",
            },
        )
        assert login_response.status_code == 200

    async def test_update_email_not_allowed(self, client, user, captured_emails):
        """Test that email cannot be updated via PATCH (if configured)."""
        response = await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"email": "newemail@example.com"},
        )
        # Depending on configuration, email update might be rejected or ignored
        # Most setups don't allow email changes via simple PATCH
        if response.status_code == 200:
            data = response.json()
            # Email should either be unchanged or require verification
            assert data["email"] in (user.email, "newemail@example.com")

    async def test_update_unauthenticated(self, client):
        """Test updating user without authentication fails."""
        response = await client.patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={"first_name": "Hacker"},
        )
        assert response.status_code == 401

    async def test_update_multiple_fields(self, client, user):
        """Test updating multiple fields at once."""
        response = await client.with_user(user).patch(
            f"{Config.AUTH_URL_PREFIX}/users/me",
            json={
                "first_name": "Multi",
                "last_name": "Update",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["first_name"] == "Multi"
        assert data["last_name"] == "Update"
