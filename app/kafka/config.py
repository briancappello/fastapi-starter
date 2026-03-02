"""Kafka consumer configurations.

Define your consumers here. Each ConsumerConfig specifies:
- name: Used for CLI filtering and health checks
- topics: List of Kafka topics to consume
- schema: Subclass of pydantic.BaseModel to use for parsing messages
- handler: Async function to process messages
- bootstrap_servers: Optional override (defaults to KAFKA_BOOTSTRAP_SERVERS)
- group_id: Optional override (defaults to "{name}-consumer")
"""

from .handlers import handle_feed_one, handle_feed_three, handle_feed_two
from .registry import ConsumerConfig
from .schema import KafkaMessageSchema


# Define all consumer configurations
CONSUMER_CONFIGS: list[ConsumerConfig] = [
    ConsumerConfig[KafkaMessageSchema](
        name="feed-one",
        topics=["feed-one-topic"],
        schema=KafkaMessageSchema,
        handler=handle_feed_one,
    ),
    ConsumerConfig[KafkaMessageSchema](
        name="feed-two",
        topics=["feed-two-topic"],
        schema=KafkaMessageSchema,
        handler=handle_feed_two,
    ),
    ConsumerConfig[KafkaMessageSchema](
        name="feed-three",
        topics=["feed-three-topic"],
        schema=KafkaMessageSchema,
        handler=handle_feed_three,
    ),
]


def get_consumer_config(name: str) -> ConsumerConfig | None:
    """Get a consumer config by name."""
    for config in CONSUMER_CONFIGS:
        if config.name == name:
            return config
    return None


def get_all_consumer_names() -> list[str]:
    """Get all registered consumer names."""
    return [c.name for c in CONSUMER_CONFIGS]
