"""Tests for email verification flow."""

import pytest

from httpx import AsyncClient

from app.config import Config
from app.mail import Email
from app.schema import UserCreate

from .conftest import find_email, get_token_from_email


class TestRequestVerify:
    """Tests for POST /auth/v1/request-verify-token"""

    async def test_request_verify_sends_email(
        self, client: AsyncClient, user_manager, captured_emails
    ):
        """Test requesting verification sends an email."""
        # Create an unverified user
        unverified = await user_manager.create(
            UserCreate(
                email="needsverify@example.com",
                password="password",
                first_name="Needs",
                last_name="Verify",
                is_verified=False,
            )
        )
        captured_emails.clear()  # Clear emails from user creation

        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/request-verify-token",
            json={"email": unverified.email},
        )
        assert response.status_code == 202

        verify_email = find_email(captured_emails, "Verify")
        assert verify_email is not None
        assert unverified.email in verify_email.recipients
        token = get_token_from_email(verify_email)
        assert token is not None

    async def test_request_verify_already_verified(
        self, client: AsyncClient, user, captured_emails
    ):
        """Test requesting verification for already verified user."""
        # user fixture is already verified
        captured_emails.clear()

        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/request-verify-token",
            json={"email": user.email},
        )
        # fastapi-users returns 202 even for already verified (security)
        assert response.status_code == 202

    async def test_request_verify_nonexistent_user(
        self, client: AsyncClient, captured_emails
    ):
        """Test requesting verification for non-existent user."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/request-verify-token",
            json={"email": "nobody@example.com"},
        )
        # Returns 202 for security (don't reveal if user exists)
        assert response.status_code == 202
        # But no email should be sent
        assert len(captured_emails) == 0


class TestVerify:
    """Tests for POST /auth/v1/verify"""

    async def test_verify_success(
        self, client: AsyncClient, user_manager, captured_emails
    ):
        """Test successful email verification."""
        # Create an unverified user
        unverified = await user_manager.create(
            UserCreate(
                email="toverify@example.com",
                password="password",
                first_name="To",
                last_name="Verify",
                is_verified=False,
            )
        )

        # Request verification via API (user_manager fixture has send_emails=False)
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/request-verify-token",
            json={"email": unverified.email},
        )

        # Get the verification token from the email
        verify_email = find_email(captured_emails, "Verify")
        assert verify_email is not None
        token = get_token_from_email(verify_email)
        assert token is not None

        # Verify the user
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/verify",
            json={"token": token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "toverify@example.com"
        assert data["is_verified"] is True

    async def test_verify_invalid_token(self, client: AsyncClient, captured_emails):
        """Test verification with invalid token fails."""
        response = await client.post(
            f"{Config.AUTH_URL_PREFIX}/verify",
            json={"token": "invalid-token-here"},
        )
        assert response.status_code == 400
        assert "VERIFY_USER_BAD_TOKEN" in response.json()["detail"]

    async def test_verify_already_verified(
        self, client: AsyncClient, user_manager, captured_emails
    ):
        """Test verification for already verified user."""
        # Create and immediately verify a user
        unverified = await user_manager.create(
            UserCreate(
                email="alreadyverified@example.com",
                password="password",
                first_name="Already",
                last_name="Verified",
                is_verified=False,
            )
        )

        # Request verification via API (user_manager fixture has send_emails=False)
        await client.post(
            f"{Config.AUTH_URL_PREFIX}/request-verify-token",
            json={"email": unverified.email},
        )

        # Get token and verify
        verify_email = find_email(captured_emails, "Verify")
        assert verify_email is not None
        token = get_token_from_email(verify_email)

        # First verification
        response1 = await client.post(
            f"{Config.AUTH_URL_PREFIX}/verify",
            json={"token": token},
        )
        assert response1.status_code == 200

        # Second verification with same token should fail
        response2 = await client.post(
            f"{Config.AUTH_URL_PREFIX}/verify",
            json={"token": token},
        )
        assert response2.status_code == 400
        assert "VERIFY_USER_ALREADY_VERIFIED" in response2.json()["detail"]
