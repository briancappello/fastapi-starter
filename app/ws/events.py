"""WebSocket event types for the event pipeline.

Defines base classes for events originating from or destined for
WebSocket connections.  These flow through the same outbox → relay →
RabbitMQ pipeline as all other events.

Inbound: WS client sends a message → handler creates a typed event →
         ``emit_event()`` writes to outbox.

Outbound: Any event source emits an event with ``target_client_ids`` →
          the ``ws_push`` handler group picks it up →
          pushes to matching WebSocket connections.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from app.events.base import Event


class WebSocketInbound(Event):
    """Base class for events originating from WebSocket clients.

    Subclasses should set ``event_type`` as a Literal and include
    the ``client_id`` that sent the message.

    Example::

        class DeviceStatusReport(WebSocketInbound):
            event_type: Literal["ws.device.status"] = "ws.device.status"
            client_id: str
            status: str
            battery: float
    """

    client_id: str


class WebSocketOutbound(Event):
    """Base class for events that should be pushed to WS clients.

    The ``target_client_ids`` field determines which connections
    receive the push.  If empty, the event is broadcast to all
    connected clients.

    Example::

        class ConfigUpdate(WebSocketOutbound):
            event_type: Literal["ws.config.updated"] = "ws.config.updated"
            target_client_ids: list[str]
            config: dict
    """

    target_client_ids: list[str] = []


# --------------------------------------------------------------------------
# Built-in events
# --------------------------------------------------------------------------


class WsClientConnected(Event):
    """Emitted when a WebSocket client connects."""

    event_type: ClassVar[str] = "ws.client.connected"
    client_id: str


class WsClientDisconnected(Event):
    """Emitted when a WebSocket client disconnects."""

    event_type: ClassVar[str] = "ws.client.disconnected"
    client_id: str
    duration_seconds: float = 0.0
