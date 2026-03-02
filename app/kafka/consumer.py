from __future__ import annotations

import asyncio
import orjson
import logging

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaConsumer, TopicPartition
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import KafkaMessage, KafkaOffset


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from .registry import ConsumerConfig

logger = logging.getLogger(__name__)


class KafkaConsumerService:
    """Async Kafka consumer with DB-managed offsets.

    - Seeks to DB-stored offsets on startup (defaults to 0 if none)
    - Processes one message per transaction
    - Updates offset in DB after successful processing
    - Uses unique constraint on (topic, partition, offset) for idempotency
    """

    def __init__(
        self,
        config: ConsumerConfig,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.config = config
        self.session_factory = session_factory
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the consumer and begin processing messages."""
        self._consumer = AIOKafkaConsumer(
            *self.config.topics,
            bootstrap_servers=self.config.get_bootstrap_servers(),
            group_id=self.config.get_group_id(),
            enable_auto_commit=False,  # We manage offsets in DB
            auto_offset_reset="earliest",  # Fallback, but we'll seek to DB offsets
        )
        await self._consumer.start()

        # Seek to DB-stored offsets
        await self._seek_to_db_offsets()

        self._task = asyncio.create_task(
            self._consume(),
            name=f"consumer:{self.config.name}",
        )

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

    async def _seek_to_db_offsets(self) -> None:
        """Seek to offsets stored in database, or 0 if none exist."""
        if not self._consumer:
            return

        # Get assigned partitions
        assignment = self._consumer.assignment()
        if not assignment:
            # Force assignment by polling
            self._consumer.subscribe(self.config.topics)
            # Need to call poll to trigger partition assignment
            await asyncio.sleep(0.1)
            assignment = self._consumer.assignment()

        async with self.session_factory() as session:
            for tp in assignment:
                # Look up offset in DB
                result = await session.execute(
                    select(KafkaOffset.offset).where(
                        KafkaOffset.consumer_name == self.config.name,
                        KafkaOffset.topic == tp.topic,
                        KafkaOffset.partition == tp.partition,
                    )
                )
                row = result.scalar_one_or_none()

                if row is not None:
                    # Seek to next offset after the last committed one
                    seek_offset = row + 1
                    logger.info(
                        f"[{self.config.name}] Seeking {tp.topic}:{tp.partition} to offset {seek_offset}"
                    )
                else:
                    # No offset in DB, start from 0
                    seek_offset = 0
                    logger.info(
                        f"[{self.config.name}] No stored offset for {tp.topic}:{tp.partition}, starting from 0"
                    )

                self._consumer.seek(tp, seek_offset)

    async def _consume(self) -> None:
        """Main consume loop - processes one message per transaction."""
        if not self._consumer:
            return

        async for message in self._consumer:
            try:
                await self._process_message(message)
            except Exception as e:
                logger.error(
                    f"[{self.config.name}] Failed on "
                    f"{message.topic}:{message.partition}@{message.offset}: {e}",
                    exc_info=True,
                )
                # Don't bump offset - message will be reprocessed on restart

    async def _process_message(self, message) -> None:
        """Process a single message in one transaction.

        1. Parse message with Pydantic
        2. Store message in DB (with conflict handling)
        3. Call handler
        4. Update offset
        5. Commit transaction
        """
        # Parse message using the schema from config
        try:
            raw_data = orjson.loads(message.value)
            parsed = self.config.schema.model_validate(raw_data)
        except Exception as e:
            logger.error(
                f"[{self.config.name}] Failed to parse message at "
                f"{message.topic}:{message.partition}@{message.offset}: {e}"
            )
            # Skip malformed messages by still updating offset
            async with self.session_factory() as session:
                await self._update_offset(session, message)
                await session.commit()
            return

        async with self.session_factory() as session:
            # Extract standard fields if present, with fallbacks
            event_name = getattr(parsed, "event_name", "unknown")
            timestamp = getattr(parsed, "timestamp", datetime.now(timezone.utc))
            # Store the full parsed model as payload if no explicit payload field
            if hasattr(parsed, "payload"):
                payload = parsed.payload
            else:
                payload = parsed.model_dump()

            # Insert message (ignore if duplicate)
            stmt = (
                pg_insert(KafkaMessage)
                .values(
                    consumer_name=self.config.name,
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                    event_name=event_name,
                    timestamp=timestamp,
                    payload=payload,
                    received_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=["topic", "partition", "offset"])
            )
            result = await session.execute(stmt)

            # If row was inserted (not a duplicate), process it
            if result.rowcount > 0:  # type: ignore[union-attr]
                await self.config.handler(parsed, session)

            # Update offset
            await self._update_offset(session, message)

            # Commit the transaction
            await session.commit()

            logger.debug(
                f"[{self.config.name}] Processed {message.topic}:{message.partition}@{message.offset}"
            )

    async def _update_offset(self, session: AsyncSession, message) -> None:
        """Upsert the current offset for this topic/partition."""
        stmt = (
            pg_insert(KafkaOffset)
            .values(
                consumer_name=self.config.name,
                topic=message.topic,
                partition=message.partition,
                offset=message.offset,
            )
            .on_conflict_do_update(
                index_elements=["consumer_name", "topic", "partition"],
                set_={"offset": message.offset},
            )
        )
        await session.execute(stmt)
