from __future__ import annotations

import asyncio
import logging
import time

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import orjson

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
        self._last_active: float = 0.0

    @property
    def last_active(self) -> float:
        """Monotonic timestamp of the last successfully processed message."""
        return self._last_active

    async def run(self) -> None:
        """Run the consumer until cancelled.

        Connects to Kafka, seeks to DB offsets, and processes messages.
        This is the primary entry point — use inside an
        ``asyncio.TaskGroup`` for structured concurrency.
        """
        self._consumer = AIOKafkaConsumer(
            *self.config.topics,
            bootstrap_servers=self.config.get_bootstrap_servers(),
            group_id=self.config.get_group_id(),
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        try:
            await self._consumer.start()
            await self._seek_to_db_offsets()

            logger.info(
                f"[{self.config.name}] Consumer started, topics={self.config.topics}"
            )

            await self._consume()
        finally:
            if self._consumer:
                await self._consumer.stop()
                self._consumer = None
            logger.info(f"[{self.config.name}] Consumer stopped")

    async def start(self) -> None:
        """Start the consumer as a detached background task.

        Convenience wrapper for contexts that manage lifecycle
        manually. Prefer :meth:`run` with a ``TaskGroup`` for
        production use.
        """
        self._task: asyncio.Task | None = asyncio.create_task(
            self.run(), name=f"consumer:{self.config.name}"
        )

    async def stop(self) -> None:
        """Stop a consumer started with :meth:`start`."""
        task = getattr(self, "_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

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
                        f"[{self.config.name}] Seeking "
                        f"{tp.topic}:{tp.partition} to offset "
                        f"{seek_offset}"
                    )
                else:
                    # No offset in DB, start from 0
                    seek_offset = 0
                    logger.info(
                        f"[{self.config.name}] No stored offset for "
                        f"{tp.topic}:{tp.partition}, starting from 0"
                    )

                self._consumer.seek(tp, seek_offset)

    async def _consume(self) -> None:
        """Main consume loop - processes one message per transaction."""
        if not self._consumer:
            return

        async for message in self._consumer:
            try:
                await self._process_message(message)
                self._last_active = time.monotonic()
            except Exception as e:
                logger.error(
                    f"[{self.config.name}] Failed on "
                    f"{message.topic}:{message.partition}@"
                    f"{message.offset}: {e}",
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

        The commit is shielded from cancellation to prevent
        processing a message but failing to record its offset,
        which would cause it to be reprocessed on restart.
        """
        # Parse message using the schema from config
        try:
            raw_data = orjson.loads(message.value)
            parsed = self.config.schema.model_validate(raw_data)
        except Exception as e:
            logger.error(
                f"[{self.config.name}] Failed to parse message at "
                f"{message.topic}:{message.partition}@{message.offset}"
                f": {e}"
            )
            # Skip malformed messages by still updating offset
            async with self.session_factory() as session:
                await self._update_offset(session, message)
                await asyncio.shield(session.commit())
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

            # Commit the transaction — shielded from cancellation
            # so we don't process + handle but fail to record offset
            await asyncio.shield(session.commit())

            logger.debug(
                f"[{self.config.name}] Processed "
                f"{message.topic}:{message.partition}@{message.offset}"
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
