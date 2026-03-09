"""Built-in WebSocket message handlers and event handlers.

Contains:
- ``ping`` message handler (heartbeat)
- ``ws_push`` event handler group (pushes events to WS clients)
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from app.events.handlers import event_handler

from .messages import MessageContext, ws_message_handler


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.events.base import Event

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Built-in WS message handlers
# --------------------------------------------------------------------------


@ws_message_handler("ping")
async def handle_ping(payload: dict, ctx: MessageContext) -> None:
    """Respond to client heartbeat ping with a pong."""
    await ctx.conn.ws.send_json({"type": "pong"})


# --------------------------------------------------------------------------
# Outbound push: event handler that forwards events to WS clients
# --------------------------------------------------------------------------


@event_handler("ws.#", group="ws_push")
async def push_to_websockets(event: Event, session: AsyncSession) -> None:
    """Push events to WebSocket clients.

    This handler runs in the ``ws_push`` worker group and receives
    events matching ``ws.#``.  It inspects the event for
    ``target_client_ids`` and pushes the serialized event to matching
    connections via the global :class:`ConnectionManager`.

    When running in a separate container, the ConnectionManager is
    local to that process — RabbitMQ delivers the event to whichever
    instance(s) have the ``ws_push`` consumer running, and each
    instance pushes to its own connected clients.
    """
    from . import get_manager

    manager = get_manager()
    if manager is None:
        # WS not enabled in this process
        return

    # Determine target clients
    target_ids: list[str] = getattr(event, "target_client_ids", [])
    data = event.model_dump(mode="json")

    if target_ids:
        sent = await manager.send_to_clients(target_ids, data)
    else:
        # No specific targets = broadcast
        sent = await manager.broadcast(data)

    if sent > 0:
        logger.debug(f"Pushed {event.event_type} to {sent} connection(s)")
