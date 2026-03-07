"""Fixtures for Kafka consumer tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class MockConsumerRecord:
    """Mock for aiokafka ConsumerRecord."""

    def __init__(
        self,
        topic: str,
        partition: int,
        offset: int,
        value: bytes,
    ):
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = value


class MockAIOKafkaConsumer:
    """Mock for AIOKafkaConsumer that yields messages from a list."""

    def __init__(self, messages: list[MockConsumerRecord]):
        self._messages = messages
        self._started = False
        self._stopped = False
        self._subscribed_topics: list[str] = []
        self._assignment: set = set()
        self._seek_positions: dict = {}

    async def start(self):
        self._started = True
        # Simulate partition assignment
        topics = set()
        for msg in self._messages:
            topics.add(msg.topic)
            self._assignment.add(MagicMock(topic=msg.topic, partition=msg.partition))

    async def stop(self):
        self._stopped = True

    def subscribe(self, topics: list[str]):
        self._subscribed_topics = topics

    def assignment(self):
        return self._assignment

    def seek(self, tp, offset: int):
        self._seek_positions[(tp.topic, tp.partition)] = offset

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


@pytest.fixture
def make_kafka_message():
    """Factory to create test Kafka messages."""

    def _make_message(
        event_name: str = "test-event",
        payload: dict | list | None = None,
        timestamp: datetime | None = None,
        topic: str = "test-topic",
        partition: int = 0,
        offset: int = 0,
    ) -> MockConsumerRecord:
        if payload is None:
            payload = {"key": "value"}
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        message_data = {
            "event_name": event_name,
            "timestamp": timestamp.isoformat(),
            "payload": payload,
        }
        return MockConsumerRecord(
            topic=topic,
            partition=partition,
            offset=offset,
            value=orjson.dumps(message_data),
        )

    return _make_message


@pytest.fixture
def mock_consumer_class(monkeypatch):
    """Factory to create a mock AIOKafkaConsumer class."""

    def _create_mock(messages: list[MockConsumerRecord]):
        mock_consumer = MockAIOKafkaConsumer(messages)

        def mock_init(*args, **kwargs):
            return mock_consumer

        # Patch the AIOKafkaConsumer class
        monkeypatch.setattr(
            "app.kafka.consumer.AIOKafkaConsumer",
            lambda *args, **kwargs: mock_consumer,
        )
        return mock_consumer

    return _create_mock


@pytest.fixture
def session_factory(session: AsyncSession):
    """Create a session factory that returns the test session."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    # Create a mock sessionmaker that returns our test session
    class MockSessionFactory:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self._session

    # Return a context manager that yields the session
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def get_session():
        yield session

    # Create a mock that behaves like async_sessionmaker
    mock_factory = MagicMock()
    mock_factory.return_value = get_session()

    return mock_factory
