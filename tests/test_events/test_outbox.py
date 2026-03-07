"""Tests for the transactional outbox writer and EventOutbox model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from sqlalchemy import select

from app.db.models.event_outbox import EventOutbox
from app.events.base import Event
from app.events.outbox import emit_event


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class UserCreatedEvent(Event):
    event_type: Literal["test.outbox.user_created"] = "test.outbox.user_created"
    user_id: int
    email: str


class TestEmitEvent:
    """Tests for the emit_event outbox writer."""

    @pytest.mark.anyio
    async def test_emit_event_inserts_to_outbox(self, session: AsyncSession):
        """Emitting an event creates a row in the outbox table."""
        event = UserCreatedEvent(
            source="test",
            user_id=1,
            email="test@example.com",
        )

        result = await emit_event(event, session)
        await session.flush()

        assert result is True

        # Check the outbox table
        stmt = select(EventOutbox)
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 1

        row = rows[0]
        assert row.event_id == event.event_id
        assert row.event_type == "test.outbox.user_created"
        assert row.source == "test"
        assert row.payload["user_id"] == 1
        assert row.payload["email"] == "test@example.com"
        assert row.relayed_at is None

    @pytest.mark.anyio
    async def test_emit_event_dedup_by_event_id(self, session: AsyncSession):
        """Emitting the same event twice is safely ignored."""
        event = UserCreatedEvent(
            source="test",
            user_id=1,
            email="test@example.com",
        )

        result1 = await emit_event(event, session)
        await session.flush()
        result2 = await emit_event(event, session)
        await session.flush()

        assert result1 is True
        assert result2 is False

        # Only one row in outbox
        stmt = select(EventOutbox)
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 1

    @pytest.mark.anyio
    async def test_emit_different_events(self, session: AsyncSession):
        """Different events create separate rows."""
        event1 = UserCreatedEvent(
            source="test",
            user_id=1,
            email="alice@example.com",
        )
        event2 = UserCreatedEvent(
            source="test",
            user_id=2,
            email="bob@example.com",
        )

        await emit_event(event1, session)
        await emit_event(event2, session)
        await session.flush()

        stmt = select(EventOutbox).order_by(EventOutbox.id)
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 2
        assert rows[0].payload["user_id"] == 1
        assert rows[1].payload["user_id"] == 2

    @pytest.mark.anyio
    async def test_emit_event_payload_contains_all_fields(self, session: AsyncSession):
        """The payload includes base Event fields and subclass fields."""
        event = UserCreatedEvent(
            source="rest:api_v1",
            user_id=42,
            email="test@example.com",
            correlation_id=uuid4(),
        )

        await emit_event(event, session)
        await session.flush()

        stmt = select(EventOutbox)
        row = (await session.execute(stmt)).scalars().one()

        payload = row.payload
        assert payload["event_type"] == "test.outbox.user_created"
        assert payload["source"] == "rest:api_v1"
        assert payload["user_id"] == 42
        assert payload["email"] == "test@example.com"
        assert "event_id" in payload
        assert "timestamp" in payload
        assert "correlation_id" in payload


class TestEventOutboxModel:
    """Tests for the EventOutbox SQLAlchemy model."""

    @pytest.mark.anyio
    async def test_outbox_row_has_timestamps(self, session: AsyncSession):
        """Outbox rows get auto-populated created_at/updated_at."""
        event = UserCreatedEvent(source="test", user_id=1, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        stmt = select(EventOutbox)
        row = (await session.execute(stmt)).scalars().one()
        assert row.created_at is not None
        assert row.updated_at is not None

    @pytest.mark.anyio
    async def test_outbox_relayed_at_default_null(self, session: AsyncSession):
        """New outbox rows have relayed_at = None."""
        event = UserCreatedEvent(source="test", user_id=1, email="test@example.com")
        await emit_event(event, session)
        await session.flush()

        stmt = select(EventOutbox)
        row = (await session.execute(stmt)).scalars().one()
        assert row.relayed_at is None
