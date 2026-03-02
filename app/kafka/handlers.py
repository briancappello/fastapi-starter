"""Message handlers for Kafka consumers.

Each handler receives a parsed KafkaMessageSchema and a database session.
The handler should perform any business logic needed for the message.
Message persistence and offset management are handled by KafkaConsumerService.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .schema import KafkaMessageSchema

logger = logging.getLogger(__name__)


async def handle_feed_one(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Handler for feed-one messages.

    Override this with your business logic.
    """
    logger.info(f"[feed-one] Received event: {message.event_name}")


async def handle_feed_two(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Handler for feed-two messages.

    Override this with your business logic.
    """
    logger.info(f"[feed-two] Received event: {message.event_name}")


async def handle_feed_three(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Handler for feed-three messages.

    Override this with your business logic.
    """
    logger.info(f"[feed-three] Received event: {message.event_name}")
