"""Tests for forgot/reset password flow."""

import pytest

from httpx import AsyncClient

from app.config import Config
from app.mail import Email

from .conftest import find_email, get_token_from_email


class TestForgotPassword:
    """Tests for POST /auth/v1/forgot-password"""

    async def test_forgot_password_sends_email(
        self, client: AsyncClient, user, captured_emails
    ):
        """Test forgot password request sends email."""
        captured_emails.clear()

        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/forgot-password",
            json={"email": user.email},
        )
        assert response.status_code == 202

        forgot_email = find_email(captured_emails, "Forgot")
        assert forgot_email is not None
        assert user.email in forgot_email.recipients
        assert forgot_email.template == "email/user-forgot-password.html"
        token = get_token_from_email(forgot_email)
        assert token is not None

    async def test_forgot_password_nonexistent_user(
        self, client: AsyncClient, captured_emails
    ):
        """Test forgot password for non-existent user."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/forgot-password",
            json={"email": "nobody@example.com"},
        )
        # Returns 202 for security (don't reveal if user exists)
        assert response.status_code == 202
        # But no email should be sent
        assert len(captured_emails) == 0


class TestResetPassword:
    """Tests for POST /auth/v1/reset-password"""

    async def test_reset_password_success(
        self, client: AsyncClient, user, captured_emails
    ):
        """Test successful password reset."""
        captured_emails.clear()

        # Request forgot password
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/forgot-password",
            json={"email": user.email},
        )

        # Get the reset token from email
        forgot_email = find_email(captured_emails, "Forgot")
        assert forgot_email is not None
        token = get_token_from_email(forgot_email)
        assert token is not None

        # Reset the password
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/reset-password",
            json={
                "token": token,
                "password": "newpassword123",
            },
        )
        assert response.status_code == 200

        # Verify can login with new password
        login_response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/jwt/login",
            data={
                "username": user.email,
                "password": "newpassword123",
            },
        )
        assert login_response.status_code == 200

    async def test_reset_password_invalid_token(
        self, client: AsyncClient, captured_emails
    ):
        """Test reset password with invalid token fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/reset-password",
            json={
                "token": "invalid-token-here",
                "password": "newpassword123",
            },
        )
        assert response.status_code == 400
        assert "RESET_PASSWORD_BAD_TOKEN" in response.json()["detail"]

    async def test_reset_password_token_reuse_fails(
        self, client: AsyncClient, user, captured_emails
    ):
        """Test that reset token can only be used once."""
        captured_emails.clear()

        # Request forgot password
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/forgot-password",
            json={"email": user.email},
        )

        forgot_email = find_email(captured_emails, "Forgot")
        token = get_token_from_email(forgot_email)

        # First reset
        response1 = await client.post(
            f"{Config.AUTH_URL_PREFIX}/reset-password",
            json={
                "token": token,
                "password": "newpassword123",
            },
        )
        assert response1.status_code == 200

        # Second reset with same token should fail
        response2 = await client.post(
            f"{Config.AUTH_URL_PREFIX}/reset-password",
            json={
                "token": token,
                "password": "anotherpassword",
            },
        )
        assert response2.status_code == 400
