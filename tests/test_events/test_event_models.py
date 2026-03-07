"""Tests for Event base model and subclassing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

import pytest

from app.events.base import Event
from app.events.registry import EventRegistry


class TestEventBaseModel:
    """Tests for the Event base Pydantic model."""

    def test_event_has_default_id(self):
        """Event gets a UUID automatically."""

        class TestEvent(Event):
            event_type: Literal["test.base_id"] = "test.base_id"

        event = TestEvent(source="test")
        assert isinstance(event.event_id, UUID)

    def test_event_has_default_timestamp(self):
        """Event gets current UTC timestamp automatically."""

        class TestEvent(Event):
            event_type: Literal["test.base_ts"] = "test.base_ts"

        before = datetime.now(timezone.utc)
        event = TestEvent(source="test")
        after = datetime.now(timezone.utc)

        assert before <= event.timestamp <= after

    def test_event_accepts_custom_id(self):
        """Event accepts a custom event_id."""

        class TestEvent(Event):
            event_type: Literal["test.custom_id"] = "test.custom_id"

        custom_id = uuid4()
        event = TestEvent(source="test", event_id=custom_id)
        assert event.event_id == custom_id

    def test_event_accepts_correlation_id(self):
        """Event accepts an optional correlation_id."""

        class TestEvent(Event):
            event_type: Literal["test.corr_id"] = "test.corr_id"

        corr_id = uuid4()
        event = TestEvent(source="test", correlation_id=corr_id)
        assert event.correlation_id == corr_id

    def test_event_correlation_id_defaults_none(self):

        class TestEvent(Event):
            event_type: Literal["test.corr_none"] = "test.corr_none"

        event = TestEvent(source="test")
        assert event.correlation_id is None

    def test_event_serialization(self):
        """Event can be serialized to dict and back."""

        class TestEvent(Event):
            event_type: Literal["test.serial"] = "test.serial"
            user_id: int
            email: str

        event = TestEvent(source="test", user_id=42, email="test@example.com")
        data = event.model_dump(mode="json")

        assert data["event_type"] == "test.serial"
        assert data["user_id"] == 42
        assert data["email"] == "test@example.com"
        assert "event_id" in data
        assert "timestamp" in data
        assert "source" in data

        # Deserialize back
        restored = TestEvent.model_validate(data)
        assert restored.event_id == event.event_id
        assert restored.user_id == 42

    def test_event_subclass_with_custom_fields(self):

        class OrderPlaced(Event):
            event_type: Literal["test.order_placed"] = "test.order_placed"
            order_id: int
            total: float
            items: list[str]

        event = OrderPlaced(
            source="rest:api_v1",
            order_id=1,
            total=99.99,
            items=["widget", "gadget"],
        )
        assert event.event_type == "test.order_placed"
        assert event.order_id == 1
        assert event.total == 99.99
        assert event.items == ["widget", "gadget"]
        assert event.source == "rest:api_v1"


class TestEventAutoRegistration:
    """Tests for auto-registration of Event subclasses."""

    def test_subclass_auto_registers(self, clean_event_registry):
        """Defining an Event subclass auto-registers it."""
        registry = clean_event_registry

        class AutoEvent(Event):
            event_type: Literal["test.auto_reg"] = "test.auto_reg"

        assert "test.auto_reg" in registry
        assert registry.get("test.auto_reg") is AutoEvent

    def test_duplicate_registration_same_class_is_ok(self, clean_event_registry):
        """Re-registering the same class is fine."""
        registry = clean_event_registry

        class DupEvent(Event):
            event_type: Literal["test.dup_ok"] = "test.dup_ok"

        # Manually re-register — should not raise
        registry.register(DupEvent)
        assert registry.get("test.dup_ok") is DupEvent

    def test_duplicate_registration_different_class_raises(self, clean_event_registry):
        """Two different classes with the same event_type raises."""
        registry = clean_event_registry

        class Event1(Event):
            event_type: Literal["test.dup_err"] = "test.dup_err"

        with pytest.raises(ValueError, match="Duplicate event_type"):

            class Event2(Event):
                event_type: Literal["test.dup_err"] = "test.dup_err"
