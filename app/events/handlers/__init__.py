"""Event handler registration and discovery.

Handlers are async functions decorated with ``@event_handler`` that
process events from the message broker.  Each handler belongs to a
*group* which maps to a RabbitMQ queue — all handlers in the same
group share the same queue (competing consumers).

Usage::

    from app.events.handlers import event_handler

    @event_handler("user.created", group="notifications")
    async def send_welcome_email(event: UserCreatedEvent, session: AsyncSession):
        ...

    @event_handler("order.*", group="analytics")
    async def track_order(event: Event, session: AsyncSession):
        ...
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.events.base import Event

logger = logging.getLogger(__name__)

# Type alias for handler functions
EventHandler = Callable[["Event", "AsyncSession"], Awaitable[None]]


@dataclass
class HandlerRegistration:
    """A registered event handler."""

    event_pattern: str  # e.g., "user.created" or "order.*"
    group: str  # RabbitMQ queue group
    handler: EventHandler
    name: str  # Fully qualified function name


class HandlerRegistry:
    """Registry of event handlers grouped by handler group.

    Handlers register with an event pattern (which becomes the
    RabbitMQ binding key) and a group name (which becomes the
    queue name).
    """

    def __init__(self) -> None:
        self._handlers: list[HandlerRegistration] = []

    def register(
        self,
        event_pattern: str,
        group: str,
        handler: EventHandler,
    ) -> None:
        """Register a handler for an event pattern in a group."""
        name = f"{handler.__module__}.{handler.__qualname__}"

        registration = HandlerRegistration(
            event_pattern=event_pattern,
            group=group,
            handler=handler,
            name=name,
        )
        self._handlers.append(registration)
        logger.debug(
            f"Registered handler {name} for '{event_pattern}' in group '{group}'"
        )

    def get_handlers_for_group(self, group: str) -> list[HandlerRegistration]:
        """Return all handlers registered for a specific group."""
        return [h for h in self._handlers if h.group == group]

    def get_groups(self) -> set[str]:
        """Return all unique handler group names."""
        return {h.group for h in self._handlers}

    def get_bindings_for_group(self, group: str) -> set[str]:
        """Return all event patterns that a group is subscribed to."""
        return {h.event_pattern for h in self._handlers if h.group == group}

    @property
    def handlers(self) -> list[HandlerRegistration]:
        """Return all registered handlers."""
        return self._handlers.copy()

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:
        groups = sorted(self.get_groups())
        return f"HandlerRegistry(groups={groups}, handlers={len(self)})"


# Global singleton
handler_registry = HandlerRegistry()


def event_handler(
    event_pattern: str,
    group: str,
) -> Callable[[EventHandler], EventHandler]:
    """Decorator to register an async function as an event handler.

    Args:
        event_pattern: The event type pattern to subscribe to.
            Supports AMQP topic patterns: ``"user.created"`` for exact match,
            ``"user.*"`` for single-word wildcard, ``"#"`` for all events.
        group: The handler group (maps to a RabbitMQ queue).
            Handlers in the same group share work (competing consumers).

    Example::

        @event_handler("user.created", group="notifications")
        async def send_welcome_email(event: UserCreatedEvent, session: AsyncSession):
            await send_email(event.email, "Welcome!")
    """

    def decorator(func: EventHandler) -> EventHandler:
        handler_registry.register(event_pattern, group, func)
        return func

    return decorator
