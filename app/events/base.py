"""Base event model and utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """Base class for all system events.

    Every event in the system must subclass this and set a unique
    ``event_type`` as a ``ClassVar[str]`` string.

    Attributes:
        event_id: Globally unique identifier for this event instance.
        event_type: Discriminator string (e.g., "user.created", "order.placed").
            Must be set as a Literal on each subclass.
        timestamp: When the event occurred.
        source: Origin of the event (e.g., "kafka:feed-one", "rest:api_v1").
        correlation_id: Optional ID for tracing chains of related events.

    Example::

        class UserCreated(Event):
            event_type: ClassVar[str] = "user.created"
            user_id: int
            email: str
    """

    event_id: UUID = Field(default_factory=uuid4)
    event_type: ClassVar[str]
    timestamp: datetime = Field(default_factory=_utcnow)
    source: str
    correlation_id: UUID | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-register subclasses that define event_type as a class attribute
        # (not just inherited from Event). Import here to avoid circular imports.
        if "event_type" in cls.__annotations__ or (
            "event_type" in cls.__dict__ and isinstance(cls.__dict__["event_type"], str)
        ):
            from .registry import event_registry

            event_registry.register(cls)
