"""Testing utilities for the event system.

Provides an in-memory broker that dispatches events directly to handlers
without requiring RabbitMQ, plus helper functions for asserting events
were emitted.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db.models.event_outbox import EventOutbox

from .handlers import handler_registry
from .registry import event_registry
from .worker import EventWorker


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .base import Event

logger = logging.getLogger(__name__)


class InMemoryBroker:
    """In-memory event broker for testing.

    Instead of publishing to RabbitMQ, events are stored in a list
    and can be dispatched directly to handlers.

    Usage in tests::

        broker = InMemoryBroker()
        await emit_event(some_event, session)
        await session.commit()

        # Manually relay from outbox
        await relay.relay_once()

        # Or dispatch directly
        await broker.dispatch_to_handlers(some_event, session)

        assert broker.published == [some_event]
    """

    def __init__(self) -> None:
        self.published: list[Event] = []
        self._started = False

    async def start(self) -> None:
        """No-op for in-memory broker."""
        self._started = True

    async def stop(self) -> None:
        """No-op for in-memory broker."""
        self._started = False

    async def publish(self, event: Event) -> None:
        """Store event in the published list."""
        self.published.append(event)

    async def publish_raw(
        self,
        body: bytes,
        event_type: str,
        event_id: str,
    ) -> None:
        """Store raw payload — used by the relay's optimized publish path.

        Deserializes back to an Event for test assertion compatibility
        (``broker.published`` is a list of Event objects).
        """
        import orjson

        payload = orjson.loads(body)
        event = event_registry.deserialize(event_type, payload)
        self.published.append(event)

    def clear(self) -> None:
        """Clear all published events."""
        self.published.clear()

    async def dispatch_to_handlers(
        self,
        event: Event,
        session: AsyncSession,
    ) -> None:
        """Directly dispatch an event to matching handlers.

        This bypasses the broker entirely and calls handlers directly,
        which is useful for integration tests.
        """
        for group in handler_registry.get_groups():
            handlers = handler_registry.get_handlers_for_group(group)
            for registration in handlers:
                if EventWorker._pattern_matches(
                    registration.event_pattern, event.event_type
                ):
                    await registration.handler(event, session)


async def get_outbox_events(
    session: AsyncSession,
    event_type: str | None = None,
) -> list[EventOutbox]:
    """Query the outbox for events, optionally filtered by type.

    Useful in tests to assert that events were emitted::

        events = await get_outbox_events(session, "user.created")
        assert len(events) == 1
        assert events[0].payload["user_id"] == 42
    """
    stmt = select(EventOutbox).order_by(EventOutbox.id)
    if event_type:
        stmt = stmt.where(EventOutbox.event_type == event_type)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def assert_event_emitted(
    session: AsyncSession,
    event_type: str,
    count: int = 1,
    **payload_checks: object,
) -> list[EventOutbox]:
    """Assert that an event was emitted to the outbox.

    Args:
        session: The database session.
        event_type: The event type to check for.
        count: Expected number of events (default 1).
        **payload_checks: Key-value pairs to check in the event payload.

    Returns:
        The matching outbox rows.

    Raises:
        AssertionError: If the count or payload doesn't match.
    """
    events = await get_outbox_events(session, event_type)
    assert len(events) == count, (
        f"Expected {count} '{event_type}' event(s), found {len(events)}"
    )

    for key, value in payload_checks.items():
        for event in events:
            assert key in event.payload, (
                f"Event payload missing key '{key}': {event.payload}"
            )
            assert event.payload[key] == value, (
                f"Event payload['{key}'] = {event.payload[key]!r}, expected {value!r}"
            )

    return events
