"""Transactional outbox writer for event emission."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.event_outbox import EventOutbox


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .base import Event

logger = logging.getLogger(__name__)


async def emit_event(event: Event, session: AsyncSession) -> bool:
    """Write an event to the transactional outbox.

    This should be called within an existing database transaction.
    The event will be picked up by the outbox relay and published
    to the message broker.

    Uses ON CONFLICT DO NOTHING so that emitting the same event
    twice (same ``event_id``) is safely ignored.

    Args:
        event: The event to emit.
        session: The active database session (will NOT be committed).

    Returns:
        True if the event was inserted, False if it was a duplicate.
    """
    stmt = (
        pg_insert(EventOutbox)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            payload=event.model_dump(mode="json"),
            source=event.source,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    result = await session.execute(stmt)
    inserted = result.rowcount > 0  # type: ignore[union-attr]

    if inserted:
        logger.debug(
            f"Emitted event {event.event_type} "
            f"(id={event.event_id}, source={event.source})"
        )
    else:
        logger.debug(f"Duplicate event {event.event_type} (id={event.event_id}) ignored")

    return inserted
