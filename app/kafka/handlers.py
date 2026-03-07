"""Message handlers for Kafka consumers.

Each handler receives a parsed KafkaMessageSchema and a database session.
The handler normalizes the Kafka message into a typed Event and emits it
to the transactional outbox. From there, the outbox relay forwards it to
RabbitMQ, and downstream event handlers process it.

This makes Kafka feeds first-class event sources — downstream handlers
receive the same typed events regardless of origin.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from app.events import emit_event

from .events import FeedOneReceived, FeedThreeReceived, FeedTwoReceived


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .schema import KafkaMessageSchema

logger = logging.getLogger(__name__)


async def handle_feed_one(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Normalize feed-one Kafka message into a typed event."""
    event = FeedOneReceived(
        source="kafka:feed-one",
        event_name=message.event_name,
        payload=message.payload,
    )
    await emit_event(event, session)
    logger.info(f"[feed-one] Emitted {event.event_type} (event_id={event.event_id})")


async def handle_feed_two(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Normalize feed-two Kafka message into a typed event."""
    event = FeedTwoReceived(
        source="kafka:feed-two",
        event_name=message.event_name,
        payload=message.payload,
    )
    await emit_event(event, session)
    logger.info(f"[feed-two] Emitted {event.event_type} (event_id={event.event_id})")


async def handle_feed_three(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Normalize feed-three Kafka message into a typed event."""
    event = FeedThreeReceived(
        source="kafka:feed-three",
        event_name=message.event_name,
        payload=message.payload,
    )
    await emit_event(event, session)
    logger.info(f"[feed-three] Emitted {event.event_type} (event_id={event.event_id})")
