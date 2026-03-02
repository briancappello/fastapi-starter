"""Tests for Kafka consumer functionality."""

from __future__ import annotations

import asyncio
import orjson

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy import select

from app.db.models import KafkaMessage, KafkaOffset
from app.kafka import ConsumerConfig, KafkaConsumerRegistry, KafkaConsumerService
from app.kafka.schema import KafkaMessageSchema


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import MockAIOKafkaConsumer, MockConsumerRecord


class TestKafkaMessageSchema:
    """Tests for the Pydantic message schema."""

    def test_valid_message_with_dict_payload(self):
        data = {
            "event_name": "user.created",
            "timestamp": "2024-01-15T10:30:00Z",
            "payload": {"user_id": 123, "email": "test@example.com"},
        }
        msg = KafkaMessageSchema.model_validate(data)
        assert msg.event_name == "user.created"
        assert isinstance(msg.payload, dict)
        assert msg.payload["user_id"] == 123

    def test_valid_message_with_list_payload(self):
        data = {
            "event_name": "batch.processed",
            "timestamp": "2024-01-15T10:30:00Z",
            "payload": [1, 2, 3, 4, 5],
        }
        msg = KafkaMessageSchema.model_validate(data)
        assert msg.event_name == "batch.processed"
        assert isinstance(msg.payload, list)
        assert len(msg.payload) == 5

    def test_invalid_message_missing_field(self):
        data = {
            "event_name": "test",
            # missing timestamp and payload
        }
        with pytest.raises(Exception):  # ValidationError
            KafkaMessageSchema.model_validate(data)


class TestConsumerConfig:
    """Tests for ConsumerConfig."""

    def test_default_group_id(self):
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        assert config.get_group_id() == "test-consumer-consumer"

    def test_custom_group_id(self):
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
            group_id="custom-group",
        )
        assert config.get_group_id() == "custom-group"

    def test_default_bootstrap_servers(self, monkeypatch):
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        # Reload config to pick up env var
        from app.config import Config

        Config.KAFKA_BOOTSTRAP_SERVERS = "kafka:9092"

        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        assert config.get_bootstrap_servers() == "kafka:9092"

    def test_custom_bootstrap_servers(self):
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
            bootstrap_servers="custom-kafka:9092",
        )
        assert config.get_bootstrap_servers() == "custom-kafka:9092"

    def test_default_schema(self):
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        assert config.schema is KafkaMessageSchema

    def test_custom_schema(self):
        from pydantic import BaseModel

        class CustomSchema(BaseModel):
            user_id: int
            action: str

        config = ConsumerConfig[CustomSchema](
            name="test-consumer",
            topics=["test-topic"],
            handler=AsyncMock(),
            schema=CustomSchema,
        )
        assert config.schema is CustomSchema


class TestKafkaConsumerRegistry:
    """Tests for KafkaConsumerRegistry."""

    def test_register_consumer(self):
        mock_factory = MagicMock()
        registry = KafkaConsumerRegistry(session_factory=mock_factory)

        config = ConsumerConfig[KafkaMessageSchema](
            name="test",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        result = registry.register(config)

        assert result is registry  # Fluent interface
        assert len(registry.configs) == 1
        assert registry.configs[0].name == "test"

    def test_should_start_all(self, monkeypatch):
        from app.config import Config

        Config.KAFKA_CONSUMERS = "all"

        mock_factory = MagicMock()
        registry = KafkaConsumerRegistry(session_factory=mock_factory)

        config = ConsumerConfig[KafkaMessageSchema](
            name="test",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        registry.register(config)

        assert registry._should_start(config) is True

    def test_should_start_none(self, monkeypatch):
        from app.config import Config

        Config.KAFKA_CONSUMERS = "none"

        mock_factory = MagicMock()
        registry = KafkaConsumerRegistry(session_factory=mock_factory)

        config = ConsumerConfig[KafkaMessageSchema](
            name="test",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )
        registry.register(config)

        assert registry._should_start(config) is False

    def test_should_start_specific(self, monkeypatch):
        from app.config import Config

        Config.KAFKA_CONSUMERS = "feed-one,feed-two"

        mock_factory = MagicMock()
        registry = KafkaConsumerRegistry(session_factory=mock_factory)

        config1 = ConsumerConfig[KafkaMessageSchema](name="feed-one", topics=["t1"],
            schema=KafkaMessageSchema, handler=AsyncMock())
        config2 = ConsumerConfig[KafkaMessageSchema](name="feed-two", topics=["t2"],
            schema=KafkaMessageSchema,handler=AsyncMock())
        config3 = ConsumerConfig[KafkaMessageSchema](name="feed-three", topics=["t3"],
            schema=KafkaMessageSchema, handler=AsyncMock())

        registry.register(config1).register(config2).register(config3)

        assert registry._should_start(config1) is True
        assert registry._should_start(config2) is True
        assert registry._should_start(config3) is False


class TestKafkaConsumerService:
    """Tests for KafkaConsumerService message processing."""

    @pytest.mark.anyio
    async def test_process_valid_message(self, session: AsyncSession, make_kafka_message):
        """Test that a valid message is stored in the database."""
        handler_called = False
        handler_message: KafkaMessageSchema | None = None

        async def test_handler(message, sess):
            nonlocal handler_called, handler_message
            handler_called = True
            handler_message = message

        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=test_handler,
        )

        # Create message
        kafka_msg = make_kafka_message(
            event_name="test.event",
            payload={"data": "test"},
            topic="test-topic",
            partition=0,
            offset=0,
        )

        # Create mock consumer that yields one message then stops
        messages = [kafka_msg]

        with patch("app.kafka.consumer.AIOKafkaConsumer") as MockConsumer:
            mock_consumer = MockAIOKafkaConsumer(messages)
            MockConsumer.return_value = mock_consumer

            # Create a proper async session factory
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)

            # Process the message directly
            await service._process_message(kafka_msg)

        # Verify handler was called
        assert handler_called
        assert handler_message.event_name == "test.event"

        # Verify message was stored in database
        result = await session.execute(select(KafkaMessage))
        stored_messages = result.scalars().all()
        assert len(stored_messages) == 1
        assert stored_messages[0].event_name == "test.event"
        assert stored_messages[0].consumer_name == "test-consumer"
        assert stored_messages[0].topic == "test-topic"
        assert stored_messages[0].offset == 0

        # Verify offset was stored
        result = await session.execute(select(KafkaOffset))
        offsets = result.scalars().all()
        assert len(offsets) == 1
        assert offsets[0].consumer_name == "test-consumer"
        assert offsets[0].offset == 0

    @pytest.mark.anyio
    async def test_duplicate_message_ignored(
        self, session: AsyncSession, make_kafka_message
    ):
        """Test that duplicate messages are not processed twice."""
        handler_call_count = 0

        async def test_handler(message, sess):
            nonlocal handler_call_count
            handler_call_count += 1

        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=test_handler,
        )

        # Create the same message twice
        kafka_msg = make_kafka_message(
            event_name="test.event",
            payload={"data": "test"},
            topic="test-topic",
            partition=0,
            offset=0,
        )

        with patch("app.kafka.consumer.AIOKafkaConsumer"):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)

            # Process the same message twice
            await service._process_message(kafka_msg)

            # Reset the factory for second call
            mock_factory.return_value = session_ctx()
            await service._process_message(kafka_msg)

        # Handler should only be called once (duplicate ignored)
        assert handler_call_count == 1

        # Should still only have one message in DB
        result = await session.execute(select(KafkaMessage))
        stored_messages = result.scalars().all()
        assert len(stored_messages) == 1

    @pytest.mark.anyio
    async def test_malformed_message_updates_offset(self, session: AsyncSession):
        """Test that malformed messages still update the offset."""
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )

        # Create a malformed message (invalid JSON structure)
        malformed_msg = MockConsumerRecord(
            topic="test-topic",
            partition=0,
            offset=5,
            value=b'{"invalid": "missing required fields"}',
        )

        with patch("app.kafka.consumer.AIOKafkaConsumer"):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)
            await service._process_message(malformed_msg)

        # Offset should still be updated (so we don't reprocess bad messages)
        result = await session.execute(select(KafkaOffset))
        offsets = result.scalars().all()
        assert len(offsets) == 1
        assert offsets[0].offset == 5

        # But no message should be stored
        result = await session.execute(select(KafkaMessage))
        messages = result.scalars().all()
        assert len(messages) == 0

    @pytest.mark.anyio
    async def test_offset_tracking_across_messages(
        self, session: AsyncSession, make_kafka_message
    ):
        """Test that offsets are updated correctly across multiple messages."""
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )

        messages = [
            make_kafka_message(offset=0, event_name="event-0"),
            make_kafka_message(offset=1, event_name="event-1"),
            make_kafka_message(offset=2, event_name="event-2"),
        ]

        with patch("app.kafka.consumer.AIOKafkaConsumer"):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()

            service = KafkaConsumerService(config, mock_factory)

            for msg in messages:
                mock_factory.return_value = session_ctx()
                await service._process_message(msg)

        # Check final offset
        result = await session.execute(select(KafkaOffset))
        offsets = result.scalars().all()
        assert len(offsets) == 1
        assert offsets[0].offset == 2  # Last processed offset

        # All messages should be stored
        result = await session.execute(select(KafkaMessage).order_by(KafkaMessage.offset))
        stored_messages = result.scalars().all()
        assert len(stored_messages) == 3
        assert [m.offset for m in stored_messages] == [0, 1, 2]

    @pytest.mark.anyio
    async def test_process_message_with_custom_schema(self, session: AsyncSession):
        """Test that messages can be processed with a custom schema."""
        from pydantic import BaseModel

        class CustomSchema(BaseModel):
            user_id: int
            action: str
            metadata: dict

        handler_called = False
        handler_message = None

        async def test_handler(message, sess):
            nonlocal handler_called, handler_message
            handler_called = True
            handler_message = message

        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            handler=test_handler,
            schema=CustomSchema,
        )

        # Create a message matching the custom schema
        custom_message_data = {
            "user_id": 123,
            "action": "login",
            "metadata": {"ip": "192.168.1.1"},
        }
        kafka_msg = MockConsumerRecord(
            topic="test-topic",
            partition=0,
            offset=0,
            value=orjson.dumps(custom_message_data),
        )

        with patch("app.kafka.consumer.AIOKafkaConsumer"):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)
            await service._process_message(kafka_msg)

        # Verify handler was called with custom schema
        assert handler_called
        assert isinstance(handler_message, CustomSchema)
        assert handler_message.user_id == 123
        assert handler_message.action == "login"

        # Verify message was stored - should use fallback values since
        # custom schema doesn't have event_name/timestamp/payload
        result = await session.execute(select(KafkaMessage))
        stored_messages = result.scalars().all()
        assert len(stored_messages) == 1
        assert stored_messages[0].event_name == "unknown"  # fallback
        # payload should be the full model dump
        assert stored_messages[0].payload == custom_message_data


class TestOffsetSeeking:
    """Tests for offset seeking on consumer startup."""

    @pytest.mark.anyio
    async def test_seek_to_stored_offset(self, session: AsyncSession):
        """Test that consumer seeks to stored offset + 1 on startup."""
        # Pre-populate an offset
        offset = KafkaOffset(
            consumer_name="test-consumer",
            topic="test-topic",
            partition=0,
            offset=10,
        )
        session.add(offset)
        await session.commit()

        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )

        with patch("app.kafka.consumer.AIOKafkaConsumer") as MockConsumer:
            mock_consumer = MockAIOKafkaConsumer([])

            # Create a proper TopicPartition mock
            tp = MagicMock()
            tp.topic = "test-topic"
            tp.partition = 0
            mock_consumer._assignment = {tp}

            MockConsumer.return_value = mock_consumer

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)
            service._consumer = mock_consumer

            await service._seek_to_db_offsets()

        # Should seek to offset 11 (stored offset + 1)
        assert ("test-topic", 0) in mock_consumer._seek_positions
        assert mock_consumer._seek_positions[("test-topic", 0)] == 11

    @pytest.mark.anyio
    async def test_seek_to_zero_when_no_offset(self, session: AsyncSession):
        """Test that consumer seeks to 0 when no offset is stored."""
        config = ConsumerConfig[KafkaMessageSchema](
            name="test-consumer",
            topics=["test-topic"],
            schema=KafkaMessageSchema,
            handler=AsyncMock(),
        )

        with patch("app.kafka.consumer.AIOKafkaConsumer") as MockConsumer:
            mock_consumer = MockAIOKafkaConsumer([])

            tp = MagicMock()
            tp.topic = "test-topic"
            tp.partition = 0
            mock_consumer._assignment = {tp}

            MockConsumer.return_value = mock_consumer

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def session_ctx():
                yield session

            mock_factory = MagicMock()
            mock_factory.return_value = session_ctx()

            service = KafkaConsumerService(config, mock_factory)
            service._consumer = mock_consumer

            await service._seek_to_db_offsets()

        # Should seek to offset 0
        assert ("test-topic", 0) in mock_consumer._seek_positions
        assert mock_consumer._seek_positions[("test-topic", 0)] == 0
