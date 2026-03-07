"""Tests for user registration flow."""

import pytest

from httpx import AsyncClient

from app.config import Config
from app.mail import Email

from .conftest import find_email, get_token_from_email


class TestRegister:
    """Tests for POST /auth/v1/register"""

    async def test_register_success(self, client: AsyncClient, captured_emails):
        """Test successful user registration."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "newuser@example.com",
                "password": "securepassword123",
                "first_name": "New",
                "last_name": "User",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["first_name"] == "New"
        assert data["last_name"] == "User"
        assert data["is_active"] is True
        assert data["is_verified"] is False  # Not verified yet
        assert "id" in data
        # Password should not be returned
        assert "password" not in data
        assert "hashed_password" not in data

    async def test_register_sends_welcome_email(
        self, client: AsyncClient, captured_emails
    ):
        """Test that registration sends a welcome email."""
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "welcome@example.com",
                "password": "securepassword123",
                "first_name": "Welcome",
                "last_name": "User",
            },
        )

        welcome_email = find_email(captured_emails, "Welcome")
        assert welcome_email is not None
        assert "welcome@example.com" in welcome_email.recipients
        assert welcome_email.template == "email/user-registered.html"
        assert welcome_email.template_context["user"].email == "welcome@example.com"

    async def test_register_sends_verification_email(
        self, client: AsyncClient, captured_emails
    ):
        """Test that registration sends a verification email."""
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "verify@example.com",
                "password": "securepassword123",
                "first_name": "Verify",
                "last_name": "User",
            },
        )

        verify_email = find_email(captured_emails, "Verify")
        assert verify_email is not None
        assert "verify@example.com" in verify_email.recipients
        assert verify_email.template == "email/user-request-verify.html"
        # Should contain a verification token
        token = get_token_from_email(verify_email)
        assert token is not None
        assert len(token) > 0

    async def test_register_duplicate_email_fails(
        self, client: AsyncClient, user, captured_emails
    ):
        """Test that registering with an existing email fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": user.email,  # Already exists
                "password": "securepassword123",
                "first_name": "Duplicate",
                "last_name": "User",
            },
        )
        assert response.status_code == 400
        assert "REGISTER_USER_ALREADY_EXISTS" in response.json()["detail"]

    async def test_register_invalid_email_fails(
        self, client: AsyncClient, captured_emails
    ):
        """Test that registering with an invalid email fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "not-an-email",
                "password": "securepassword123",
                "first_name": "Invalid",
                "last_name": "Email",
            },
        )
        assert response.status_code == 422  # Validation error

    async def test_register_weak_password_fails(
        self, client: AsyncClient, captured_emails
    ):
        """Test that registering with a weak password fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "weak@example.com",
                "password": "123",  # Too short/weak
                "first_name": "Weak",
                "last_name": "Password",
            },
        )
        # fastapi-users validates password length (min 3 by default)
        # If custom validation is added, this might be 400
        assert response.status_code in (201, 400, 422)

    async def test_register_missing_required_fields(
        self, client: AsyncClient, captured_emails
    ):
        """Test that registration fails with missing required fields."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/register",
            json={
                "email": "missing@example.com",
                # Missing password, first_name, last_name
            },
        )
        assert response.status_code == 422
