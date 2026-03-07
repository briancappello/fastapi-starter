"""Tests for the EventRegistry."""

from __future__ import annotations

from typing import Literal

import pytest

from app.events.base import Event
from app.events.registry import EventRegistry


class TestEventRegistry:
    """Tests for EventRegistry lookup and deserialization."""

    def test_register_and_get(self):
        """Can register a class and look it up."""
        registry = EventRegistry()

        class MyEvent(Event):
            event_type: Literal["test.reg_get"] = "test.reg_get"

        registry.register(MyEvent)
        assert registry.get("test.reg_get") is MyEvent

    def test_get_unknown_returns_none(self):
        registry = EventRegistry()
        assert registry.get("nonexistent") is None

    def test_deserialize_valid(self):
        """Can deserialize a payload into a typed Event."""
        registry = EventRegistry()

        class UserCreated(Event):
            event_type: Literal["test.deser"] = "test.deser"
            user_id: int
            email: str

        registry.register(UserCreated)

        payload = {
            "event_type": "test.deser",
            "source": "test",
            "user_id": 42,
            "email": "test@example.com",
        }
        event = registry.deserialize("test.deser", payload)

        assert isinstance(event, UserCreated)
        assert event.user_id == 42
        assert event.email == "test@example.com"

    def test_deserialize_unknown_type_raises_keyerror(self):
        registry = EventRegistry()

        with pytest.raises(KeyError, match="Unknown event type"):
            registry.deserialize("nope", {})

    def test_deserialize_invalid_payload_raises_validation_error(self):
        registry = EventRegistry()

        class StrictEvent(Event):
            event_type: Literal["test.strict"] = "test.strict"
            required_field: int

        registry.register(StrictEvent)

        with pytest.raises(Exception):  # Pydantic ValidationError
            registry.deserialize(
                "test.strict",
                {"source": "test"},  # missing required_field
            )

    def test_event_types_property(self):
        registry = EventRegistry()

        class A(Event):
            event_type: Literal["test.a"] = "test.a"

        class B(Event):
            event_type: Literal["test.b"] = "test.b"

        registry.register(A)
        registry.register(B)

        types = registry.event_types
        assert "test.a" in types
        assert "test.b" in types
        assert types["test.a"] is A
        assert types["test.b"] is B

    def test_len(self):
        registry = EventRegistry()
        assert len(registry) == 0

        class A(Event):
            event_type: Literal["test.len_a"] = "test.len_a"

        registry.register(A)
        assert len(registry) == 1

    def test_contains(self):
        registry = EventRegistry()

        class A(Event):
            event_type: Literal["test.contains"] = "test.contains"

        registry.register(A)
        assert "test.contains" in registry
        assert "nonexistent" not in registry
