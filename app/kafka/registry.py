from __future__ import annotations

import asyncio
import logging
import time

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Generic, TypeVar

from pydantic import BaseModel

from app.config import Config


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from .consumer import KafkaConsumerService

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class ConsumerConfig(Generic[T]):
    """Configuration for a Kafka consumer.

    Attributes:
        name: Unique identifier used for CLI, env var filtering, and health checks.
        topics: List of Kafka topics to consume.
        handler: Async function to process each message. Signature: (message, session) -> None
        schema: Pydantic model class for parsing messages. Defaults to KafkaMessageSchema.
        bootstrap_servers: Kafka broker addresses. If None, uses KAFKA_BOOTSTRAP_SERVERS env var.
        group_id: Kafka consumer group ID. If None, defaults to "{name}-consumer".
    """

    name: str
    topics: list[str]
    schema: type[T]
    handler: Callable[[T, AsyncSession], Awaitable[None]]
    bootstrap_servers: list[str] | str | None = None
    group_id: str | None = None

    def get_bootstrap_servers(self) -> str:
        """Return bootstrap servers, falling back to global config."""
        return self.bootstrap_servers or Config.KAFKA_BOOTSTRAP_SERVERS

    def get_group_id(self) -> str:
        """Return group ID, defaulting to {name}-consumer."""
        return self.group_id or f"{self.name}-consumer"


@dataclass
class KafkaConsumerRegistry:
    """Manages lifecycle of multiple Kafka consumers.

    Consumers are registered with configs, then started/stopped together
    based on KAFKA_CONSUMERS env var filtering.
    """

    session_factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession]
    _configs: list[ConsumerConfig] = field(default_factory=list)
    _consumers: list[KafkaConsumerService] = field(default_factory=list)

    def register(self, config: ConsumerConfig) -> KafkaConsumerRegistry:
        """Register a consumer config. Returns self for fluent chaining."""
        self._configs.append(config)
        return self

    def _should_start(self, config: ConsumerConfig) -> bool:
        """Check if consumer should start based on KAFKA_CONSUMERS setting."""
        consumers_setting = Config.KAFKA_CONSUMERS.strip().lower()
        if consumers_setting == "all":
            return True
        if consumers_setting == "none":
            return False
        allowed = [c.strip() for c in consumers_setting.split(",")]
        return config.name in allowed

    async def run_all(self, tg: asyncio.TaskGroup) -> None:
        """Start all matching consumers as tasks in the given TaskGroup.

        This is the preferred entry point for production use with
        structured concurrency. Each consumer's ``run()`` coroutine
        is launched as a task in the provided ``TaskGroup``.

        Args:
            tg: The TaskGroup to create consumer tasks in.
        """
        if not Config.KAFKA_ENABLED:
            logger.info("Kafka is disabled (KAFKA_ENABLED=false)")
            return

        from .consumer import KafkaConsumerService

        for config in self._configs:
            if self._should_start(config):
                consumer = KafkaConsumerService(config, self.session_factory)
                self._consumers.append(consumer)
                tg.create_task(
                    consumer.run(),
                    name=f"kafka:{config.name}",
                )
                logger.info(f"Starting consumer: {config.name}")

    async def start_all(self) -> None:
        """Start all registered consumers as detached background tasks.

        Convenience wrapper for contexts that manage lifecycle
        manually. Prefer :meth:`run_all` with a ``TaskGroup`` for
        production use.
        """
        if not Config.KAFKA_ENABLED:
            logger.info("Kafka is disabled (KAFKA_ENABLED=false)")
            return

        from .consumer import KafkaConsumerService

        for config in self._configs:
            if self._should_start(config):
                consumer = KafkaConsumerService(config, self.session_factory)
                self._consumers.append(consumer)
                logger.info(f"Starting consumer: {config.name}")

        await asyncio.gather(*[c.start() for c in self._consumers])

    async def stop_all(self) -> None:
        """Stop all running consumers."""
        if self._consumers:
            logger.info(f"Stopping {len(self._consumers)} consumers...")
            await asyncio.gather(*[c.stop() for c in self._consumers])
            self._consumers.clear()

    def get_status(self) -> dict[str, dict]:
        """Return status dict for all consumers (for health endpoint)."""
        return {
            c.config.name: {
                "running": (
                    hasattr(c, "_task") and c._task is not None and not c._task.done()
                ),
                "last_active": c.last_active,
                "topics": c.config.topics,
                "bootstrap_servers": c.config.get_bootstrap_servers(),
                "group_id": c.config.get_group_id(),
            }
            for c in self._consumers
        }

    @property
    def consumers(self) -> list[KafkaConsumerService]:
        """Return all active consumer instances."""
        return self._consumers.copy()

    @property
    def configs(self) -> list[ConsumerConfig]:
        """Return all registered configs."""
        return self._configs.copy()
