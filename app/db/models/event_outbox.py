from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from .base import Base, Mapped, mapped_column, pk


class EventOutbox(Base):
    """Transactional outbox for event-driven architecture.

    Events are written here in the same DB transaction as the domain
    operation that produces them.  A relay process reads un-relayed
    rows and publishes them to the message broker.

    The ``event_id`` UNIQUE constraint provides deduplication —
    attempting to emit the same event twice is safely ignored.
    """

    id: Mapped[pk]
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        unique=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(nullable=False, index=True)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(nullable=False)
    relayed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
    )


# Partial index for efficient relay queries: only un-relayed rows
Index(
    "ix_event_outbox_pending",
    EventOutbox.id,
    postgresql_where=text("relayed_at IS NULL"),
)
