"""Tests for the hello module — web server event emission pattern.

Verifies that POST /hello emits an event to the outbox in the same
transaction as the route's response, and that the handler is properly
registered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sqlalchemy import select

from app.db.models.event_outbox import EventOutbox
from app.events.handlers import handler_registry
from app.events.testing import assert_event_emitted
from app.hello import HelloEvent


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.client import TestClient


class TestHelloEvent:
    """Test the HelloEvent definition."""

    def test_hello_event_type(self):
        event = HelloEvent(source="test", name="Alice", message="Hi")
        assert event.event_type == "hello.greeted"

    def test_hello_event_fields(self):
        event = HelloEvent(
            source="rest:hello",
            name="Alice",
            message="Hello, Alice!",
        )
        assert event.name == "Alice"
        assert event.message == "Hello, Alice!"
        assert event.source == "rest:hello"
        assert event.event_id is not None

    def test_hello_event_registered(self):
        from app.events.registry import event_registry

        cls = event_registry.get("hello.greeted")
        assert cls is HelloEvent


class TestHelloEndpoint:
    """Test the POST /hello endpoint."""

    @pytest.mark.anyio
    async def test_hello_returns_response(self, client: TestClient):
        response = await client.post("/hello", params={"name": "World"})
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Hello!, World!"
        assert "event_id" in data

    @pytest.mark.anyio
    async def test_hello_emits_event_to_outbox(
        self, client: TestClient, session: AsyncSession
    ):
        response = await client.post("/hello", params={"name": "Test"})
        assert response.status_code == 200

        # The event should be in the outbox
        result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == "hello.greeted")
        )
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].payload["name"] == "Test"
        assert rows[0].source == "rest:hello"

    @pytest.mark.anyio
    async def test_hello_custom_message(self, client: TestClient):
        response = await client.post(
            "/hello",
            params={"name": "Alice", "message": "Greetings"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Greetings, Alice!"


class TestHelloHandler:
    """Test that the hello event handler is registered."""

    def test_handler_registered(self):
        """The on_hello_greeted handler should be in the 'hello' group."""
        # Need to import to trigger registration
        import app.hello  # noqa: F401

        groups = handler_registry.get_groups()
        assert "hello" in groups

        handlers = handler_registry.get_handlers_for_group("hello")
        names = [h.name for h in handlers]
        assert any("on_hello_greeted" in n for n in names)

    def test_handler_pattern(self):
        import app.hello  # noqa: F401

        bindings = handler_registry.get_bindings_for_group("hello")
        assert "hello.greeted" in bindings
