"""Tests for Kafka → outbox event integration.

Verifies that Kafka handlers emit typed events to the transactional outbox.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from sqlalchemy import select

from app.db.models.event_outbox import EventOutbox
from app.kafka.events import (
    FeedOneReceived,
    FeedThreeReceived,
    FeedTwoReceived,
)
from app.kafka.handlers import (
    handle_feed_one,
    handle_feed_three,
    handle_feed_two,
)
from app.kafka.schema import KafkaMessageSchema


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TestKafkaEventDefinitions:
    """Test that Kafka event types are properly defined and registered."""

    def test_feed_one_event_type(self):
        event = FeedOneReceived(source="test", event_name="e", payload={})
        assert event.event_type == "kafka.feed_one.received"

    def test_feed_two_event_type(self):
        event = FeedTwoReceived(source="test", event_name="e", payload={})
        assert event.event_type == "kafka.feed_two.received"

    def test_feed_three_event_type(self):
        event = FeedThreeReceived(source="test", event_name="e", payload={})
        assert event.event_type == "kafka.feed_three.received"

    def test_events_auto_registered(self):
        from app.events.registry import event_registry

        assert "kafka.feed_one.received" in event_registry
        assert "kafka.feed_two.received" in event_registry
        assert "kafka.feed_three.received" in event_registry

    def test_feed_event_fields(self):
        event = FeedOneReceived(
            source="kafka:feed-one",
            event_name="test.event",
            payload={"key": "value"},
        )
        assert event.event_name == "test.event"
        assert event.payload == {"key": "value"}
        assert event.source == "kafka:feed-one"
        assert event.event_id is not None
        assert event.timestamp is not None


class TestKafkaHandlersEmitEvents:
    """Test that Kafka handlers emit events to the outbox."""

    @pytest.mark.anyio
    async def test_feed_one_handler_emits_event(self, session: AsyncSession):
        message = KafkaMessageSchema(
            event_name="user.signup",
            timestamp=datetime.now(timezone.utc),
            payload={"user_id": 42},
        )

        await handle_feed_one(message, session)
        await session.flush()

        result = await session.execute(select(EventOutbox))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].event_type == "kafka.feed_one.received"
        assert rows[0].source == "kafka:feed-one"
        assert rows[0].payload["event_name"] == "user.signup"
        assert rows[0].payload["payload"] == {"user_id": 42}

    @pytest.mark.anyio
    async def test_feed_two_handler_emits_event(self, session: AsyncSession):
        message = KafkaMessageSchema(
            event_name="order.placed",
            timestamp=datetime.now(timezone.utc),
            payload={"order_id": 99},
        )

        await handle_feed_two(message, session)
        await session.flush()

        result = await session.execute(select(EventOutbox))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].event_type == "kafka.feed_two.received"
        assert rows[0].source == "kafka:feed-two"

    @pytest.mark.anyio
    async def test_feed_three_handler_emits_event(self, session: AsyncSession):
        message = KafkaMessageSchema(
            event_name="item.updated",
            timestamp=datetime.now(timezone.utc),
            payload=[1, 2, 3],
        )

        await handle_feed_three(message, session)
        await session.flush()

        result = await session.execute(select(EventOutbox))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].event_type == "kafka.feed_three.received"
        assert rows[0].source == "kafka:feed-three"
        assert rows[0].payload["payload"] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_handler_event_contains_kafka_metadata(self, session: AsyncSession):
        """Verify the emitted event payload preserves Kafka message fields."""
        message = KafkaMessageSchema(
            event_name="data.sync",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            payload={"records": [{"id": 1}]},
        )

        await handle_feed_one(message, session)
        await session.flush()

        result = await session.execute(select(EventOutbox))
        row = result.scalar_one()

        # The payload should contain the full event model dump
        assert row.payload["event_name"] == "data.sync"
        assert row.payload["source"] == "kafka:feed-one"
        assert "event_id" in row.payload
        assert "timestamp" in row.payload

    @pytest.mark.anyio
    async def test_duplicate_event_ids_ignored(self, session: AsyncSession):
        """Verify that emit_event's dedup prevents duplicate outbox rows."""
        from uuid import uuid4

        from app.events import emit_event

        event_id = uuid4()
        event1 = FeedOneReceived(
            event_id=event_id,
            source="kafka:feed-one",
            event_name="test",
            payload={},
        )
        event2 = FeedOneReceived(
            event_id=event_id,
            source="kafka:feed-one",
            event_name="test",
            payload={},
        )

        result1 = await emit_event(event1, session)
        result2 = await emit_event(event2, session)
        await session.flush()

        assert result1 is True
        assert result2 is False

        rows = (await session.execute(select(EventOutbox))).scalars().all()
        assert len(list(rows)) == 1
