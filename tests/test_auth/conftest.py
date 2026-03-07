"""Auth test fixtures - email capture and helpers."""

from unittest.mock import AsyncMock, patch

import pytest

from app.mail import Email


@pytest.fixture
def captured_emails() -> list[Email]:
    """
    Fixture that captures all emails sent during a test.

    Automatically mocks FastMail.send_message to intercept emails
    instead of sending them.

    Usage:
        def test_something(client, captured_emails):
            # ... do something that sends email ...
            assert len(captured_emails) == 1
            assert captured_emails[0].subject == "Welcome!"
            assert "token" in captured_emails[0].template_context
    """
    emails: list[Email] = []

    async def mock_send_message(self, message: Email) -> None:
        emails.append(message)

    with patch("app.mail.FastMail.send_message", mock_send_message):
        yield emails


def find_email(emails: list[Email], subject_contains: str) -> Email | None:
    """Helper to find an email by subject substring."""
    for email in emails:
        if subject_contains.lower() in email.subject.lower():
            return email
    return None


def get_token_from_email(email: Email) -> str | None:
    """Extract token from email template context."""
    if email.template_context:
        return email.template_context.get("token")
    return None
