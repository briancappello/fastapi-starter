from datetime import datetime
from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from .base import Base, Mapped, mapped_column, pk


class ProcessedEvent(Base):
    """Tracks which events have been processed by which handler groups.

    Used for exactly-once semantics at the handler level.  When a handler
    processes an event that mutates state, it inserts a row here in the
    same transaction.  On redelivery, the UNIQUE constraint prevents
    duplicate processing.
    """

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "handler_group",
            name="uq_processed_event_event_id_handler_group",
        ),
    )

    id: Mapped[pk]
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    handler_group: Mapped[str] = mapped_column(nullable=False, index=True)
