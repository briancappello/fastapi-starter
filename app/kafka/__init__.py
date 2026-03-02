"""Kafka consumer integration.

Usage:
    from app.kafka import registry, CONSUMER_CONFIGS

    # Register all consumers
    for config in CONSUMER_CONFIGS:
        registry.register(config)

    # In lifespan
    await registry.start_all()
    yield
    await registry.stop_all()
"""

from .config import CONSUMER_CONFIGS, get_all_consumer_names, get_consumer_config
from .consumer import KafkaConsumerService
from .handlers import handle_feed_one, handle_feed_three, handle_feed_two
from .registry import ConsumerConfig, KafkaConsumerRegistry
from .schema import KafkaMessageSchema


__all__ = [
    "CONSUMER_CONFIGS",
    "ConsumerConfig",
    "KafkaConsumerRegistry",
    "KafkaConsumerService",
    "KafkaMessageSchema",
    "get_all_consumer_names",
    "get_consumer_config",
    "handle_feed_one",
    "handle_feed_two",
    "handle_feed_three",
]
