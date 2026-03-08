"""Event broker abstraction and RabbitMQ implementation."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import aio_pika

from app.config import Config


if TYPE_CHECKING:
    from .base import Event

logger = logging.getLogger(__name__)


@runtime_checkable
class EventBroker(Protocol):
    """Protocol for event broker backends.

    Implementations must support publishing events and managing
    their connection lifecycle.
    """

    async def publish(self, event: Event) -> None:
        """Publish an event to the broker."""
        ...

    async def start(self) -> None:
        """Connect to the broker and declare topology."""
        ...

    async def stop(self) -> None:
        """Disconnect from the broker."""
        ...


class RabbitMQBroker:
    """RabbitMQ event broker using a topic exchange.

    Events are published with ``routing_key = event.event_type``
    (e.g., ``"user.created"``).  Consumers bind queues with patterns
    like ``"user.*"`` or ``"#"`` to receive matching events.

    Topology (declared on :meth:`start`):

    - Exchange ``events`` (topic, durable)
    - Exchange ``events.retry`` (direct, durable)
    - Exchange ``events.dlx`` (direct, durable)

    Per handler-group queues are declared by the worker, not the broker.
    """

    EXCHANGE_NAME = "events"
    RETRY_EXCHANGE_NAME = "events.retry"
    DLX_EXCHANGE_NAME = "events.dlx"

    def __init__(self, url: str | None = None) -> None:
        self._url = url or Config.RABBITMQ_URL
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._publish_channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def start(self) -> None:
        """Connect to RabbitMQ and declare exchanges.

        Opens two channels:
        - A main channel with publisher confirms (used by the worker for
          queue declarations and consuming).
        - A publish channel **without** publisher confirms for fire-and-forget
          relay publishing.  The outbox already guarantees durability — if
          RabbitMQ drops a message the relay will re-send it from the
          un-relayed outbox row.
        """
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()

        # Publish channel: no publisher confirms → publish() is fire-and-forget
        self._publish_channel = await self._connection.channel(
            publisher_confirms=False,
        )

        # Main topic exchange (declared on the main channel so the worker
        # can bind queues; the publish channel sees the same exchange)
        self._exchange = await self._channel.declare_exchange(
            self.EXCHANGE_NAME,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        # Also get a reference on the publish channel for fire-and-forget sends
        self._publish_exchange = await self._publish_channel.declare_exchange(
            self.EXCHANGE_NAME,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        # Retry exchange (handlers NACK → retry queue → back to main)
        await self._channel.declare_exchange(
            self.RETRY_EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # Dead letter exchange (max retries exceeded)
        await self._channel.declare_exchange(
            self.DLX_EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        logger.info(f"RabbitMQ broker connected to {self._url}")

    async def stop(self) -> None:
        """Close channels and connection."""
        for ch in (self._publish_channel, self._channel):
            if ch and not ch.is_closed:
                await ch.close()
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

        self._exchange = None
        self._publish_channel = None
        self._channel = None
        self._connection = None

        logger.info("RabbitMQ broker disconnected")

    async def publish(self, event: Event) -> None:
        """Publish an event to the topic exchange (fire-and-forget).

        Uses the no-confirms publish channel for maximum throughput.
        The outbox guarantees durability — if the message is lost,
        the relay will re-send from the un-relayed outbox row.

        The routing key is the event's ``event_type`` (e.g., ``"user.created"``),
        which allows consumers to bind with topic patterns.
        """
        if self._publish_exchange is None:
            raise RuntimeError("Broker not started. Call start() before publish().")

        import orjson

        body = orjson.dumps(event.model_dump(mode="json"))
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={
                "event_type": event.event_type,
                "event_id": str(event.event_id),
                "x-retry-count": 0,
            },
        )

        await self._publish_exchange.publish(
            message,
            routing_key=event.event_type,
        )
        logger.debug(
            f"Published {event.event_type} (id={event.event_id}) "
            f"to exchange '{self.EXCHANGE_NAME}'"
        )

    @property
    def channel(self) -> aio_pika.abc.AbstractChannel:
        """Return the active channel (for worker queue declarations)."""
        if self._channel is None:
            raise RuntimeError("Broker not started.")
        return self._channel
