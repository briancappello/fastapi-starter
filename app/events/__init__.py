"""Event-driven architecture for the application.

This package implements a transactional outbox pattern with support for
multiple event sources and handler groups. Events are normalized into
typed Pydantic models, written to a database outbox, relayed to a message
broker (RabbitMQ), and consumed by registered handlers.

Usage:
    from app.events import Event, emit_event, event_handler

    # Define an event
    class OrderPlaced(Event):
        event_type: Literal["order.placed"] = "order.placed"
        order_id: int
        total: Decimal

    # Emit an event (within a DB transaction)
    await emit_event(OrderPlaced(source="rest:api_v1", order_id=1, total=99), session)

    # Handle an event
    @event_handler("order.placed", group="notifications")
    async def send_order_confirmation(event: OrderPlaced, session: AsyncSession):
        ...
"""

from .base import Event
from .outbox import emit_event
from .registry import EventRegistry, event_registry


__all__ = [
    "Event",
    "EventRegistry",
    "emit_event",
    "event_registry",
]
