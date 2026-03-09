"""Outbox relay: reads un-relayed events from the DB and publishes to broker.

Uses PostgreSQL LISTEN/NOTIFY for low-latency notification of new events,
with a polling fallback to catch any missed notifications.
"""

from __future__ import annotations

import asyncio
import logging
import time

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
        publish_timeout: Seconds before a single publish is considered
            failed.  Prevents a hanging broker connection from holding
            row locks indefinitely.
    """

    def __init__(
        self,
        broker: EventBroker,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 100,
        poll_interval: float = 5.0,
        publish_timeout: float = 30.0,
    ) -> None:
        self._broker = broker
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._publish_timeout = publish_timeout
        self._notify_event = asyncio.Event()
        self._last_active: float = 0.0

    @property
    def last_active(self) -> float:
        """Monotonic timestamp of the last successful loop iteration."""
        return self._last_active

    async def run(self) -> None:
        """Run the relay until cancelled.

        This is the primary entry point. It runs the PG listener and
        relay loop as concurrent tasks inside an ``asyncio.TaskGroup``.
        If either task crashes, the TaskGroup cancels the other and
        propagates the exception.

        Usage from lifespan (via TaskGroup)::

            async with asyncio.TaskGroup() as tg:
                tg.create_task(relay.run())

        Usage from CLI::

            task = asyncio.create_task(relay.run())
            await shutdown_event.wait()
            task.cancel()
        """
        logger.info("Outbox relay started")
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._listen_notifications())
                tg.create_task(self._relay_loop())
        except* Exception as eg:
            # Log all errors from the group, then re-raise the first
            for exc in eg.exceptions:
                if not isinstance(exc, asyncio.CancelledError):
                    logger.error("Outbox relay task failed", exc_info=exc)
            raise eg.exceptions[0] from None
        finally:
            logger.info("Outbox relay stopped")

    async def start(self) -> None:
        """Start the relay as a detached background task.

        Convenience wrapper for contexts that manage lifecycle
        manually (e.g. tests, simple scripts). Prefer :meth:`run`
        with a ``TaskGroup`` for production use.
        """
        self._task: asyncio.Task | None = asyncio.create_task(
            self.run(), name="outbox-relay"
        )

    async def stop(self) -> None:
        """Stop a relay started with :meth:`start`."""
        task = getattr(self, "_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

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

        while True:
            try:
                conn = await asyncpg.connect(url)
                try:
                    await conn.add_listener(
                        OUTBOX_NOTIFY_CHANNEL,
                        self._on_notify,
                    )
                    logger.info(f"Listening on PG channel '{OUTBOX_NOTIFY_CHANNEL}'")

                    # Keep the connection alive
                    while True:
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
        while True:
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

                # Relay pending events
                relayed = await self._relay_batch()
                # If we relayed a full batch, there might be more
                while relayed == self._batch_size:
                    relayed = await self._relay_batch()

                self._last_active = time.monotonic()

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

        The final commit (marking events as relayed) is shielded from
        cancellation to prevent inconsistency: if events have been
        published to the broker but the commit is cancelled, they
        would be re-relayed on next startup (duplicate delivery).

        Returns the number of events relayed.
        """
        from app.metrics import (
            RELAY_BATCH_DURATION,
            RELAY_BATCH_SIZE,
            RELAY_BATCH_TOTAL,
            RELAY_COMMIT_DURATION,
            RELAY_EVENTS_TOTAL,
            RELAY_PENDING_GAUGE,
            RELAY_PUBLISH_DURATION,
            RELAY_PUBLISH_ERRORS,
        )

        batch_start = time.monotonic()

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

            RELAY_BATCH_TOTAL.inc()

            if not rows:
                RELAY_BATCH_SIZE.observe(0)
                RELAY_BATCH_DURATION.observe(time.monotonic() - batch_start)
                return 0

            RELAY_PENDING_GAUGE.set(len(rows))

            # Phase 1: Prepare raw payloads for publishing.
            # The outbox already stores the event as a JSONB dict, so we
            # serialize directly with orjson — no Pydantic round-trip needed.
            import orjson

            rows_to_publish: list[tuple[EventOutbox, bytes]] = []
            relayed_ids: list[int] = []

            for row in rows:
                body = orjson.dumps(row.payload)
                rows_to_publish.append((row, body))

            # Phase 2: Publish all events concurrently
            if rows_to_publish:
                publish_start = time.monotonic()

                async def _publish_one(row: EventOutbox, body: bytes):
                    return await asyncio.wait_for(
                        self._broker.publish_raw(
                            body=body,
                            event_type=row.event_type,
                            event_id=str(row.event_id),
                        ),
                        timeout=self._publish_timeout,
                    )

                publish_results = await asyncio.gather(
                    *(_publish_one(row, body) for row, body in rows_to_publish),
                    return_exceptions=True,
                )
                RELAY_PUBLISH_DURATION.observe(time.monotonic() - publish_start)

                for (row, _body), pub_result in zip(rows_to_publish, publish_results):
                    if isinstance(pub_result, Exception):
                        RELAY_PUBLISH_ERRORS.inc()
                        logger.exception(
                            f"Failed to relay outbox row {row.id} "
                            f"({row.event_type}), will retry",
                            exc_info=pub_result,
                        )
                        # Don't mark as relayed — will be retried next batch.
                    else:
                        relayed_ids.append(row.id)

            # Phase 3: Bulk-mark all successfully relayed rows
            # Shielded from cancellation so we don't publish to the broker
            # but fail to mark rows, causing duplicates on restart.
            if relayed_ids:
                commit_start = time.monotonic()
                await asyncio.shield(self._mark_relayed(session, relayed_ids))
                RELAY_COMMIT_DURATION.observe(time.monotonic() - commit_start)

            relayed_count = len(relayed_ids)
            RELAY_BATCH_SIZE.observe(relayed_count)
            RELAY_EVENTS_TOTAL.inc(relayed_count)
            RELAY_BATCH_DURATION.observe(time.monotonic() - batch_start)

            if relayed_count > 0:
                logger.debug(f"Relayed {relayed_count} events from outbox")
            return relayed_count

    @staticmethod
    async def _mark_relayed(session: AsyncSession, relayed_ids: list[int]) -> None:
        """Mark outbox rows as relayed and commit.

        Extracted as a separate method so it can be wrapped with
        ``asyncio.shield``.
        """
        await session.execute(
            update(EventOutbox)
            .where(EventOutbox.id.in_(relayed_ids))
            .values(relayed_at=datetime.now(timezone.utc))
        )
        await session.commit()

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
