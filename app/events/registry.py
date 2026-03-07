"""Event type registry for deserialization and discovery."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .base import Event

logger = logging.getLogger(__name__)


class EventRegistry:
    """Registry mapping event_type strings to Event subclasses.

    Subclasses of :class:`Event` are auto-registered when they define an
    ``event_type`` class attribute or annotation.  The registry is used
    by the outbox relay and worker to deserialize events from their JSON
    payload back into typed Pydantic models.

    Usage::

        from app.events.registry import event_registry

        # Auto-registered via Event.__init_subclass__
        class UserCreated(Event):
            event_type: Literal["user.created"] = "user.created"
            ...

        # Look up and deserialize
        event_cls = event_registry.get("user.created")
        event = event_registry.deserialize("user.created", payload_dict)
    """

    def __init__(self) -> None:
        self._registry: dict[str, type[Event]] = {}

    def register(self, event_cls: type[Event]) -> None:
        """Register an event class by its event_type.

        Called automatically by :meth:`Event.__init_subclass__`.
        """
        event_type = self._resolve_event_type(event_cls)
        if event_type is None:
            return

        if event_type in self._registry:
            existing = self._registry[event_type]
            if existing is not event_cls:
                raise ValueError(
                    f"Duplicate event_type '{event_type}': "
                    f"{event_cls.__name__} conflicts with {existing.__name__}"
                )
            return

        self._registry[event_type] = event_cls
        logger.debug(f"Registered event type '{event_type}' -> {event_cls.__name__}")

    def get(self, event_type: str) -> type[Event] | None:
        """Look up an event class by event_type string."""
        return self._registry.get(event_type)

    def deserialize(self, event_type: str, payload: dict) -> Event:
        """Deserialize a payload dict into a typed Event instance.

        Raises:
            KeyError: If the event_type is not registered.
            pydantic.ValidationError: If the payload doesn't match the schema.
        """
        event_cls = self._registry.get(event_type)
        if event_cls is None:
            raise KeyError(
                f"Unknown event type '{event_type}'. "
                f"Registered types: {sorted(self._registry.keys())}"
            )
        return event_cls.model_validate(payload)

    @property
    def event_types(self) -> dict[str, type[Event]]:
        """Return a copy of the registry mapping."""
        return self._registry.copy()

    def _resolve_event_type(self, event_cls: type[Event]) -> str | None:
        """Extract the event_type value from a class.

        Handles both ``event_type: Literal["foo"] = "foo"`` and
        ``event_type = "foo"`` patterns.
        """
        # Check for a class-level default value
        if isinstance(event_cls.__dict__.get("event_type"), str):
            return event_cls.__dict__["event_type"]

        # Check model fields (Pydantic v2)
        if hasattr(event_cls, "model_fields"):
            field = event_cls.model_fields.get("event_type")
            if field is not None and field.default is not None:
                return field.default

        return None

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, event_type: str) -> bool:
        return event_type in self._registry

    def __repr__(self) -> str:
        types = sorted(self._registry.keys())
        return f"EventRegistry({types})"


# Global singleton
event_registry = EventRegistry()
