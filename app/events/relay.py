"""Outbox relay: reads un-relayed events from the DB and publishes to broker.

Uses PostgreSQL LISTEN/NOTIFY for low-latency notification of new events,
with a polling fallback to catch any missed notifications.
"""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.config import Config
from app.db.models.event_outbox import EventOutbox


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from .broker import EventBroker

logger = logging.getLogger(__name__)

# Channel name used for PG LISTEN/NOTIFY
OUTBOX_NOTIFY_CHANNEL = "event_outbox_channel"


class OutboxRelay:
    """Relay service that bridges the outbox table to the event broker.

    1. Listens for PG NOTIFY on ``event_outbox_channel`` (fired by a
       DB trigger on INSERT into ``event_outbox``).
    2. On notification, fetches un-relayed rows using SELECT ... FOR UPDATE
       SKIP LOCKED (safe for concurrent relays).
    3. Publishes each event to the broker.
    4. Marks rows as relayed (sets ``relayed_at``).
    5. A fallback poll runs every ``poll_interval`` seconds to catch any
       notifications missed during relay downtime or connection issues.

    Args:
        broker: The event broker to publish to.
        session_factory: Async session factory for DB access.
        batch_size: Max events to relay per batch.
        poll_interval: Seconds between fallback polls.
    """

    def __init__(
        self,
        broker: EventBroker,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 100,
        poll_interval: float = 5.0,
    ) -> None:
        self._broker = broker
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._notify_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the relay (listener + poll loop)."""
        self._running = True
        self._listener_task = asyncio.create_task(
            self._listen_notifications(),
            name="outbox-relay-listener",
        )
        self._task = asyncio.create_task(
            self._relay_loop(),
            name="outbox-relay-loop",
        )
        logger.info("Outbox relay started")

    async def stop(self) -> None:
        """Stop the relay gracefully."""
        self._running = False
        self._notify_event.set()  # Wake up the loop so it can exit

        for task in (self._task, self._listener_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._task = None
        self._listener_task = None
        logger.info("Outbox relay stopped")

    async def _listen_notifications(self) -> None:
        """Listen for PG NOTIFY using raw asyncpg connection.

        This runs on a dedicated connection (not from the session pool)
        because LISTEN requires a persistent connection.
        """
        import asyncpg

        # Parse connection URL from SQLAlchemy format to asyncpg format
        url = Config.SQL_DB_URL
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

        while self._running:
            try:
                conn = await asyncpg.connect(url)
                try:
                    await conn.add_listener(
                        OUTBOX_NOTIFY_CHANNEL,
                        self._on_notify,
                    )
                    logger.info(f"Listening on PG channel '{OUTBOX_NOTIFY_CHANNEL}'")

                    # Keep the connection alive while running
                    while self._running:
                        await asyncio.sleep(1)
                finally:
                    await conn.remove_listener(
                        OUTBOX_NOTIFY_CHANNEL,
                        self._on_notify,
                    )
                    await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PG listener connection failed, retrying in 5s")
                await asyncio.sleep(5)

    def _on_notify(
        self,
        connection: object,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        """Callback for PG NOTIFY — signals the relay loop to wake up."""
        self._notify_event.set()

    async def _relay_loop(self) -> None:
        """Main loop: wait for notification or poll interval, then relay."""
        while self._running:
            try:
                # Wait for either a notification or the poll interval
                try:
                    await asyncio.wait_for(
                        self._notify_event.wait(),
                        timeout=self._poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass  # Poll interval elapsed — relay anyway

                self._notify_event.clear()

                if not self._running:
                    break

                # Relay pending events
                relayed = await self._relay_batch()
                # If we relayed a full batch, there might be more
                while relayed == self._batch_size and self._running:
                    relayed = await self._relay_batch()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in relay loop, retrying in 1s")
                await asyncio.sleep(1)

    async def _relay_batch(self) -> int:
        """Fetch and relay one batch of pending outbox events.

        Publishes all events in a batch concurrently via
        :func:`asyncio.gather` and marks them relayed with a single
        bulk UPDATE, maximising throughput.

        Returns the number of events relayed.
        """
        from .registry import event_registry

        async with self._session_factory() as session:
            # SELECT pending rows with row-level locking
            # SKIP LOCKED allows concurrent relay instances
            stmt = (
                select(EventOutbox)
                .where(EventOutbox.relayed_at.is_(None))
                .order_by(EventOutbox.id)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                return 0

            # Phase 1: Deserialize all events up-front
            events_to_publish: list[tuple[EventOutbox, Event]] = []
            relayed_ids: list[int] = []

            for row in rows:
                try:
                    event = event_registry.deserialize(row.event_type, row.payload)
                    events_to_publish.append((row, event))
                except KeyError:
                    logger.error(
                        f"Unknown event type '{row.event_type}' "
                        f"in outbox row {row.id}, skipping"
                    )
                    # Mark unknown types as relayed to prevent infinite retry
                    relayed_ids.append(row.id)

            # Phase 2: Publish all events concurrently
            if events_to_publish:
                publish_results = await asyncio.gather(
                    *(self._broker.publish(event) for _, event in events_to_publish),
                    return_exceptions=True,
                )

                for (row, _event), pub_result in zip(events_to_publish, publish_results):
                    if isinstance(pub_result, Exception):
                        logger.exception(
                            f"Failed to relay outbox row {row.id} "
                            f"({row.event_type}), will retry",
                            exc_info=pub_result,
                        )
                        # Don't mark as relayed — will be retried next batch.
                        # Unlike the sequential approach we don't break here;
                        # other publishes in the gather may have succeeded.
                    else:
                        relayed_ids.append(row.id)

            # Phase 3: Bulk-mark all successfully relayed rows
            if relayed_ids:
                await session.execute(
                    update(EventOutbox)
                    .where(EventOutbox.id.in_(relayed_ids))
                    .values(relayed_at=datetime.now(timezone.utc))
                )

            await session.commit()
            relayed_count = len(relayed_ids)
            if relayed_count > 0:
                logger.debug(f"Relayed {relayed_count} events from outbox")
            return relayed_count

    async def relay_once(self) -> int:
        """Relay all pending events (for testing/manual invocation).

        Returns total number of events relayed.
        """
        total = 0
        while True:
            count = await self._relay_batch()
            total += count
            if count < self._batch_size:
                break
        return total
