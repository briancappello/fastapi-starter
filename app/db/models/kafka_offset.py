from sqlalchemy import UniqueConstraint

from .base import Base, Mapped, mapped_column, pk


class KafkaOffset(Base):
    """Tracks consumer offsets per topic/partition.

    Used for manual offset management - consumers seek to these
    offsets on startup instead of relying on Kafka's internal offset storage.
    """

    __table_args__ = (
        UniqueConstraint(
            "consumer_name",
            "topic",
            "partition",
            name="uq_kafka_offset_consumer_topic_partition",
        ),
    )

    id: Mapped[pk]
    consumer_name: Mapped[str] = mapped_column(index=True)
    topic: Mapped[str]
    partition: Mapped[int]
    offset: Mapped[int]
