"""End-to-end integration tests for the event system.

Tests the full flow: emit_event -> outbox -> relay -> handler dispatch.
Uses InMemoryBroker to avoid requiring RabbitMQ.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.event_outbox import EventOutbox
from app.db.models.processed_event import ProcessedEvent
from app.events.base import Event
from app.events.handlers import HandlerRegistry, handler_registry
from app.events.outbox import emit_event
from app.events.relay import OutboxRelay
from app.events.testing import (
    InMemoryBroker,
    assert_event_emitted,
    get_outbox_events,
)


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class UserCreated(Event):
    event_type: Literal["test.integ.user_created"] = "test.integ.user_created"
    user_id: int
    email: str


class OrderPlaced(Event):
    event_type: Literal["test.integ.order_placed"] = "test.integ.order_placed"
    order_id: int
    total: float


class TestEndToEndFlow:
    """Tests for the complete event pipeline."""

    @pytest.mark.anyio
    async def test_emit_relay_dispatch(
        self,
        session: AsyncSession,
        session_factory,
        clean_handler_registry,
        in_memory_broker: InMemoryBroker,
    ):
        """Full flow: emit -> relay -> handler receives event."""
        handler_calls: list[Event] = []

        async def track_handler(event: Event, sess: AsyncSession):
            handler_calls.append(event)

        clean_handler_registry.register(
            "test.integ.user_created", "test-group", track_handler
        )

        # Step 1: Emit event
        event = UserCreated(
            source="rest:api_v1",
            user_id=42,
            email="test@example.com",
        )
        await emit_event(event, session)
        await session.commit()

        # Step 2: Relay from outbox to broker
        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        relayed = await relay.relay_once()
        assert relayed == 1

        # Step 3: Dispatch from broker to handlers
        published_event = in_memory_broker.published[0]
        await in_memory_broker.dispatch_to_handlers(published_event, session)

        # Verify handler was called with correct event
        assert len(handler_calls) == 1
        received = handler_calls[0]
        assert isinstance(received, UserCreated)
        assert received.user_id == 42
        assert received.email == "test@example.com"
        assert received.event_id == event.event_id

    @pytest.mark.anyio
    async def test_multiple_handlers_same_event(
        self,
        session: AsyncSession,
        session_factory,
        clean_handler_registry,
        in_memory_broker: InMemoryBroker,
    ):
        """Multiple handler groups receive the same event."""
        group_a_calls: list[Event] = []
        group_b_calls: list[Event] = []

        async def handler_a(event: Event, sess: AsyncSession):
            group_a_calls.append(event)

        async def handler_b(event: Event, sess: AsyncSession):
            group_b_calls.append(event)

        clean_handler_registry.register("test.integ.user_created", "group-a", handler_a)
        clean_handler_registry.register("test.integ.user_created", "group-b", handler_b)

        event = UserCreated(source="test", user_id=1, email="a@example.com")
        await emit_event(event, session)
        await session.commit()

        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        await relay.relay_once()

        published_event = in_memory_broker.published[0]
        await in_memory_broker.dispatch_to_handlers(published_event, session)

        assert len(group_a_calls) == 1
        assert len(group_b_calls) == 1

    @pytest.mark.anyio
    async def test_wildcard_handler_receives_multiple_types(
        self,
        session: AsyncSession,
        session_factory,
        clean_handler_registry,
        in_memory_broker: InMemoryBroker,
    ):
        """A handler with wildcard pattern receives multiple event types."""
        received: list[Event] = []

        async def catch_all(event: Event, sess: AsyncSession):
            received.append(event)

        clean_handler_registry.register("test.integ.*", "catch-all", catch_all)

        event1 = UserCreated(source="test", user_id=1, email="a@example.com")
        event2 = OrderPlaced(source="test", order_id=1, total=99.99)
        await emit_event(event1, session)
        await emit_event(event2, session)
        await session.commit()

        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        await relay.relay_once()

        for published in in_memory_broker.published:
            await in_memory_broker.dispatch_to_handlers(published, session)

        assert len(received) == 2
        assert isinstance(received[0], UserCreated)
        assert isinstance(received[1], OrderPlaced)


class TestTestingHelpers:
    """Tests for the testing utility functions."""

    @pytest.mark.anyio
    async def test_get_outbox_events(self, session: AsyncSession):
        event = UserCreated(source="test", user_id=1, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        events = await get_outbox_events(session)
        assert len(events) == 1

        events = await get_outbox_events(session, "test.integ.user_created")
        assert len(events) == 1

        events = await get_outbox_events(session, "nonexistent")
        assert len(events) == 0

    @pytest.mark.anyio
    async def test_assert_event_emitted(self, session: AsyncSession):
        event = UserCreated(source="test", user_id=42, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        # Should pass
        rows = await assert_event_emitted(
            session,
            "test.integ.user_created",
            count=1,
            user_id=42,
        )
        assert len(rows) == 1

    @pytest.mark.anyio
    async def test_assert_event_emitted_wrong_count(self, session: AsyncSession):
        event = UserCreated(source="test", user_id=1, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        with pytest.raises(AssertionError, match="Expected 2"):
            await assert_event_emitted(
                session,
                "test.integ.user_created",
                count=2,
            )

    @pytest.mark.anyio
    async def test_assert_event_emitted_wrong_payload(self, session: AsyncSession):
        event = UserCreated(source="test", user_id=1, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        with pytest.raises(AssertionError, match="expected 99"):
            await assert_event_emitted(
                session,
                "test.integ.user_created",
                user_id=99,
            )


class TestProcessedEventDedup:
    """Tests for handler dedup using the ProcessedEvent model."""

    @pytest.mark.anyio
    async def test_processed_event_insert(self, session: AsyncSession):
        """Can insert a processed_event record."""
        from uuid import uuid4

        event_id = uuid4()
        pe = ProcessedEvent(event_id=event_id, handler_group="test-group")
        session.add(pe)
        await session.flush()

        stmt = select(ProcessedEvent)
        row = (await session.execute(stmt)).scalars().one()
        assert row.event_id == event_id
        assert row.handler_group == "test-group"

    @pytest.mark.anyio
    async def test_processed_event_unique_constraint(self, session: AsyncSession):
        """Duplicate (event_id, handler_group) is rejected."""
        from uuid import uuid4

        from sqlalchemy.exc import IntegrityError

        event_id = uuid4()
        pe1 = ProcessedEvent(event_id=event_id, handler_group="test-group")
        session.add(pe1)
        await session.flush()

        pe2 = ProcessedEvent(event_id=event_id, handler_group="test-group")
        session.add(pe2)
        with pytest.raises(IntegrityError):
            await session.flush()

    @pytest.mark.anyio
    async def test_same_event_different_groups_ok(self, session: AsyncSession):
        """Same event_id with different handler_group is allowed."""
        from uuid import uuid4

        event_id = uuid4()
        pe1 = ProcessedEvent(event_id=event_id, handler_group="group-a")
        pe2 = ProcessedEvent(event_id=event_id, handler_group="group-b")
        session.add(pe1)
        session.add(pe2)
        await session.flush()

        stmt = select(ProcessedEvent).where(ProcessedEvent.event_id == event_id)
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 2
