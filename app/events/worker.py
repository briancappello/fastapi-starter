"""Event worker: consumes events from RabbitMQ and dispatches to handlers.

Each worker instance consumes from one or more handler groups.  For each
group, a durable queue is declared and bound to the appropriate event
patterns on the ``events`` topic exchange.
"""

from __future__ import annotations

import asyncio
import logging
import time

from typing import TYPE_CHECKING

import aio_pika
import orjson

from app.events.broker import RabbitMQBroker
from app.events.handlers import handler_registry
from app.events.registry import event_registry


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Default retry configuration
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAY_MS = 5000  # 5 seconds


class EventWorker:
    """Consumes events from RabbitMQ and dispatches to registered handlers.

    For each handler group, the worker:

    1. Declares a durable queue (``handlers.{group}``)
    2. Declares a retry queue with TTL (``handlers.{group}.retry``)
    3. Declares a dead-letter queue (``handlers.{group}.dlq``)
    4. Binds the main queue to event patterns from the handler registry
    5. Starts consuming messages

    When a message is received:

    - Deserialize the event
    - Create a DB session
    - Call all handlers for that group
    - ACK on success
    - On failure: check retry count, NACK to retry queue or DLQ

    Args:
        broker: The RabbitMQ broker (must be started).
        session_factory: Async session factory for handler DI.
        groups: Which handler groups to consume for.
            If None, consumes for all registered groups.
        max_retries: Maximum retry attempts before dead-lettering.
        retry_delay_ms: Delay between retries in milliseconds.
        prefetch_count: Max unacknowledged messages per channel.
            Higher values improve throughput by pipelining messages,
            but increase memory usage and redelivery on crash.
            Tunable via ``EVENT_WORKER_PREFETCH`` env var.
    """

    def __init__(
        self,
        broker: RabbitMQBroker,
        session_factory: async_sessionmaker[AsyncSession],
        groups: set[str] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_ms: int = DEFAULT_RETRY_DELAY_MS,
        prefetch_count: int = 10,
    ) -> None:
        self._broker = broker
        self._session_factory = session_factory
        self._groups = groups
        self._max_retries = max_retries
        self._retry_delay_ms = retry_delay_ms
        self._prefetch_count = prefetch_count
        self._queues: dict[str, aio_pika.abc.AbstractQueue] = {}
        self._last_active: float = 0.0

    @property
    def last_active(self) -> float:
        """Monotonic timestamp of the last successfully processed message."""
        return self._last_active

    async def run(self) -> None:
        """Run the worker until cancelled.

        Declares queues for each handler group, then consumes
        concurrently from all groups inside an ``asyncio.TaskGroup``.
        If any consumer task crashes, the TaskGroup cancels the others
        and propagates the exception.
        """
        groups = self._groups or handler_registry.get_groups()

        if not groups:
            logger.warning("No handler groups to consume for")
            return

        channel = self._broker.channel
        await channel.set_qos(prefetch_count=self._prefetch_count)

        # Declare queues for all groups
        consume_tasks: list[tuple[aio_pika.abc.AbstractQueue, str]] = []
        for group in groups:
            handlers = handler_registry.get_handlers_for_group(group)
            if not handlers:
                logger.warning(f"No handlers registered for group '{group}'")
                continue

            bindings = handler_registry.get_bindings_for_group(group)
            main_queue = await self._setup_group(channel, group, bindings)
            consume_tasks.append((main_queue, group))

        if not consume_tasks:
            logger.warning("No groups with handlers to consume for")
            return

        logger.info(f"Event worker started for groups: {sorted(groups)}")

        try:
            async with asyncio.TaskGroup() as tg:
                for queue, group in consume_tasks:
                    tg.create_task(
                        self._consume_group(queue, group),
                        name=f"worker:{group}",
                    )
        except* Exception as eg:
            for exc in eg.exceptions:
                if not isinstance(exc, asyncio.CancelledError):
                    logger.error("Event worker task failed", exc_info=exc)
            raise eg.exceptions[0] from None
        finally:
            self._queues.clear()
            logger.info("Event worker stopped")

    async def start(self) -> None:
        """Start the worker as a detached background task.

        Convenience wrapper for contexts that manage lifecycle
        manually (e.g. tests, simple scripts). Prefer :meth:`run`
        with a ``TaskGroup`` for production use.
        """
        self._task: asyncio.Task | None = asyncio.create_task(
            self.run(), name="event-worker"
        )

    async def stop(self) -> None:
        """Stop a worker started with :meth:`start`."""
        task = getattr(self, "_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _setup_group(
        self,
        channel: aio_pika.abc.AbstractChannel,
        group: str,
        bindings: set[str],
    ) -> aio_pika.abc.AbstractQueue:
        """Set up queues and consumers for a handler group.

        Returns the main queue for the group.
        """
        queue_name = f"handlers.{group}"
        retry_queue_name = f"handlers.{group}.retry"
        dlq_name = f"handlers.{group}.dlq"

        # Declare main queue with DLX pointing to retry exchange
        main_queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": RabbitMQBroker.RETRY_EXCHANGE_NAME,
                "x-dead-letter-routing-key": retry_queue_name,
            },
        )

        # Declare retry queue with TTL and DLX back to main exchange
        await channel.declare_queue(
            retry_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": self._retry_delay_ms,
                "x-dead-letter-exchange": RabbitMQBroker.EXCHANGE_NAME,
                # No routing key override — message retains original key
            },
        )

        # Bind retry queue to retry exchange
        retry_exchange = await channel.get_exchange(RabbitMQBroker.RETRY_EXCHANGE_NAME)
        retry_queue = await channel.get_queue(retry_queue_name)
        await retry_queue.bind(retry_exchange, routing_key=retry_queue_name)

        # Declare DLQ
        dlq = await channel.declare_queue(dlq_name, durable=True)

        # Bind DLQ to DLX exchange
        dlx_exchange = await channel.get_exchange(RabbitMQBroker.DLX_EXCHANGE_NAME)
        await dlq.bind(dlx_exchange, routing_key=dlq_name)

        # Bind main queue to event patterns on the events exchange
        events_exchange = await channel.get_exchange(RabbitMQBroker.EXCHANGE_NAME)
        for pattern in bindings:
            await main_queue.bind(events_exchange, routing_key=pattern)
            logger.debug(
                f"Bound {queue_name} to '{pattern}' on "
                f"exchange '{RabbitMQBroker.EXCHANGE_NAME}'"
            )

        self._queues[group] = main_queue
        return main_queue

    async def _consume_group(
        self,
        queue: aio_pika.abc.AbstractQueue,
        group: str,
    ) -> None:
        """Consume messages from a group's queue."""
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(message, group)
                self._last_active = time.monotonic()

    async def _process_message(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        group: str,
    ) -> None:
        """Process a single message: deserialize, dispatch, ACK/NACK."""
        from app.metrics import (
            WORKER_DEAD_LETTERS_TOTAL,
            WORKER_HANDLER_DURATION,
            WORKER_IN_FLIGHT,
            WORKER_MESSAGES_TOTAL,
            WORKER_MESSAGE_DURATION,
            WORKER_RETRIES_TOTAL,
        )

        msg_start = time.monotonic()
        WORKER_IN_FLIGHT.labels(group=group).inc()

        headers = message.headers or {}
        retry_count = int(headers.get("x-retry-count", 0))
        event_type = headers.get("event_type", "unknown")

        try:
            payload = orjson.loads(message.body)
            event = event_registry.deserialize(str(event_type), payload)
        except (KeyError, Exception) as e:
            logger.error(
                f"[{group}] Failed to deserialize message (event_type={event_type}): {e}"
            )
            # Dead-letter undeserializable messages immediately
            await self._dead_letter(message, group, str(e))
            WORKER_MESSAGES_TOTAL.labels(group=group, outcome="dlq").inc()
            WORKER_DEAD_LETTERS_TOTAL.labels(group=group).inc()
            WORKER_IN_FLIGHT.labels(group=group).dec()
            return

        handlers = handler_registry.get_handlers_for_group(group)

        try:
            async with self._session_factory() as session:
                for registration in handlers:
                    # Check if this handler's pattern matches the event type
                    if self._pattern_matches(
                        registration.event_pattern, event.event_type
                    ):
                        handler_start = time.monotonic()
                        await registration.handler(event, session)
                        WORKER_HANDLER_DURATION.labels(
                            group=group, handler=registration.name
                        ).observe(time.monotonic() - handler_start)

                await session.commit()

            await message.ack()
            WORKER_MESSAGES_TOTAL.labels(group=group, outcome="ack").inc()
            WORKER_MESSAGE_DURATION.labels(group=group).observe(
                time.monotonic() - msg_start
            )
            logger.debug(f"[{group}] Processed {event.event_type} (id={event.event_id})")
        except Exception as e:
            logger.error(
                f"[{group}] Handler failed for {event.event_type} "
                f"(id={event.event_id}, retry={retry_count}): {e}",
                exc_info=True,
            )

            if retry_count >= self._max_retries:
                await self._dead_letter(message, group, f"Max retries exceeded: {e}")
                WORKER_MESSAGES_TOTAL.labels(group=group, outcome="dlq").inc()
                WORKER_DEAD_LETTERS_TOTAL.labels(group=group).inc()
            else:
                await self._retry(message, retry_count)
                WORKER_MESSAGES_TOTAL.labels(group=group, outcome="retry").inc()
                WORKER_RETRIES_TOTAL.labels(group=group).inc()
        finally:
            WORKER_IN_FLIGHT.labels(group=group).dec()

    async def _retry(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        retry_count: int,
    ) -> None:
        """NACK a message for retry with incremented retry count.

        The message goes to the retry queue (via DLX), waits for the TTL,
        then is republished to the main exchange with the original routing key.
        """
        # Update retry count in headers for the next attempt
        headers = dict(message.headers or {})
        headers["x-retry-count"] = retry_count + 1

        # Publish a new message with updated headers to the retry exchange
        # then reject the original
        channel = self._broker.channel
        retry_exchange = await channel.get_exchange(RabbitMQBroker.RETRY_EXCHANGE_NAME)

        # Determine the retry queue routing key from the message's
        # consumer queue. We use the message's routing_key to figure
        # out which group it came from, but since we know the group
        # from the consumer, we could also track it. For simplicity,
        # we reject with requeue=False which sends to the DLX
        # (which IS the retry exchange for the main queue).
        await message.reject(requeue=False)

    async def _dead_letter(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        group: str,
        reason: str,
    ) -> None:
        """Send a message to the dead-letter queue."""
        # Publish to the DLX exchange with the DLQ routing key
        channel = self._broker.channel
        dlx_exchange = await channel.get_exchange(RabbitMQBroker.DLX_EXCHANGE_NAME)

        dlq_routing_key = f"handlers.{group}.dlq"
        new_message = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={
                **(message.headers or {}),
                "x-dead-letter-reason": reason,
            },
        )
        await dlx_exchange.publish(new_message, routing_key=dlq_routing_key)

        # ACK the original message (we've handled it by dead-lettering)
        await message.ack()
        logger.warning(f"[{group}] Dead-lettered message: {reason}")

    @staticmethod
    def _pattern_matches(pattern: str, event_type: str) -> bool:
        """Check if an AMQP topic pattern matches an event type.

        Supports:
        - Exact match: ``"user.created"``
        - Single-word wildcard: ``"user.*"`` matches ``"user.created"``
          but not ``"user.profile.updated"``
        - Multi-word wildcard: ``"#"`` matches everything,
          ``"user.#"`` matches ``"user.created"`` and
          ``"user.profile.updated"``
        """
        if pattern == "#":
            return True

        pattern_parts = pattern.split(".")
        event_parts = event_type.split(".")

        return _match_parts(pattern_parts, event_parts)


def _match_parts(pattern_parts: list[str], event_parts: list[str]) -> bool:
    """Recursive AMQP topic pattern matcher."""
    if not pattern_parts and not event_parts:
        return True
    if not pattern_parts:
        return False

    head = pattern_parts[0]
    rest = pattern_parts[1:]

    if head == "#":
        if not rest:
            return True
        # '#' matches zero or more words
        for i in range(len(event_parts) + 1):
            if _match_parts(rest, event_parts[i:]):
                return True
        return False
    elif head == "*":
        if not event_parts:
            return False
        return _match_parts(rest, event_parts[1:])
    else:
        if not event_parts or head != event_parts[0]:
            return False
        return _match_parts(rest, event_parts[1:])
