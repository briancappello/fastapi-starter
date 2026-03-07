"""Tests for the outbox relay with InMemoryBroker."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import pytest

from sqlalchemy import select, update

from app.db.models.event_outbox import EventOutbox
from app.events.base import Event
from app.events.outbox import emit_event
from app.events.relay import OutboxRelay
from app.events.testing import InMemoryBroker


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RelayTestEvent(Event):
    event_type: Literal["test.relay.event"] = "test.relay.event"
    data: str


class TestOutboxRelay:
    """Tests for OutboxRelay using InMemoryBroker."""

    @pytest.mark.anyio
    async def test_relay_pending_events(
        self,
        session: AsyncSession,
        session_factory,
        in_memory_broker: InMemoryBroker,
    ):
        """Relay picks up un-relayed events and publishes them."""
        # Emit events to outbox
        event1 = RelayTestEvent(source="test", data="hello")
        event2 = RelayTestEvent(source="test", data="world")
        await emit_event(event1, session)
        await emit_event(event2, session)
        await session.commit()

        # Create relay and relay once
        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        count = await relay.relay_once()

        assert count == 2
        assert len(in_memory_broker.published) == 2
        assert in_memory_broker.published[0].event_id == event1.event_id
        assert in_memory_broker.published[1].event_id == event2.event_id

    @pytest.mark.anyio
    async def test_relay_marks_rows_as_relayed(
        self,
        session: AsyncSession,
        session_factory,
        in_memory_broker: InMemoryBroker,
    ):
        """Relayed rows get relayed_at set."""
        event = RelayTestEvent(source="test", data="hello")
        await emit_event(event, session)
        await session.commit()

        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        await relay.relay_once()

        # Check the outbox row
        stmt = select(EventOutbox)
        row = (await session.execute(stmt)).scalars().one()
        assert row.relayed_at is not None

    @pytest.mark.anyio
    async def test_relay_skips_already_relayed(
        self,
        session: AsyncSession,
        session_factory,
        in_memory_broker: InMemoryBroker,
    ):
        """Already-relayed rows are not relayed again."""
        event = RelayTestEvent(source="test", data="hello")
        await emit_event(event, session)
        await session.commit()

        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )

        count1 = await relay.relay_once()
        assert count1 == 1

        count2 = await relay.relay_once()
        assert count2 == 0
        assert len(in_memory_broker.published) == 1

    @pytest.mark.anyio
    async def test_relay_empty_outbox(
        self,
        session: AsyncSession,
        session_factory,
        in_memory_broker: InMemoryBroker,
    ):
        """Relay with no pending events returns 0."""
        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
        )
        count = await relay.relay_once()
        assert count == 0
        assert len(in_memory_broker.published) == 0

    @pytest.mark.anyio
    async def test_relay_respects_batch_size(
        self,
        session: AsyncSession,
        session_factory,
        in_memory_broker: InMemoryBroker,
    ):
        """Relay processes up to batch_size events per batch."""
        # Emit 5 events
        for i in range(5):
            await emit_event(
                RelayTestEvent(source="test", data=f"event-{i}"),
                session,
            )
        await session.commit()

        # Create relay with batch_size=2
        relay = OutboxRelay(
            broker=in_memory_broker,
            session_factory=session_factory,
            batch_size=2,
        )

        # relay_once should process all events (it loops until < batch_size)
        count = await relay.relay_once()
        assert count == 5
        assert len(in_memory_broker.published) == 5


class TestInMemoryBroker:
    """Tests for the InMemoryBroker test utility."""

    @pytest.mark.anyio
    async def test_publish_stores_events(self):
        broker = InMemoryBroker()
        event = RelayTestEvent(source="test", data="hello")

        await broker.publish(event)

        assert len(broker.published) == 1
        assert broker.published[0] is event

    @pytest.mark.anyio
    async def test_clear(self):
        broker = InMemoryBroker()
        await broker.publish(RelayTestEvent(source="test", data="hello"))
        broker.clear()
        assert len(broker.published) == 0

    @pytest.mark.anyio
    async def test_start_stop(self):
        broker = InMemoryBroker()
        await broker.start()
        assert broker._started
        await broker.stop()
        assert not broker._started
