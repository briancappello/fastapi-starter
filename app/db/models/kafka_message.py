from datetime import datetime
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base, Mapped, mapped_column, pk


class KafkaMessage(Base):
    """Stores received Kafka messages.

    Each message is persisted with its metadata and payload.
    The unique constraint on (topic, partition, offset) prevents duplicates
    if a consumer restarts and reprocesses messages.
    """

    __table_args__ = (
        UniqueConstraint(
            "topic", "partition", "offset", name="uq_kafka_message_topic_partition_offset"
        ),
    )

    id: Mapped[pk]
    consumer_name: Mapped[str] = mapped_column(index=True)
    topic: Mapped[str] = mapped_column(index=True)
    partition: Mapped[int]
    offset: Mapped[int]
    event_name: Mapped[str] = mapped_column(index=True)
    timestamp: Mapped[datetime]
    payload: Mapped[Any] = mapped_column(JSONB)
    received_at: Mapped[datetime]
