"""WebSocket server package.

Provides a WebSocket endpoint at ``/ws/{client_id}`` with:
- Configurable authentication (none, HTTP Basic, token, first message)
- Generic message routing via ``@ws_message_handler``
- Application-level heartbeat with stale connection reaping
- Integration with the event pipeline (inbound -> outbox, outbound -> push)

The ``ConnectionManager`` and ``HeartbeatManager`` are module-level
singletons, created on first access via :func:`init_ws` (called from
the app lifespan).

Usage in ``app/main.py``::

    from app.ws import router as ws_router, init_ws, shutdown_ws

    # In lifespan:
    manager, heartbeat = await init_ws()
    app.include_router(ws_router)
    # heartbeat.run() is launched in the TaskGroup
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from .endpoint import websocket_endpoint
from .heartbeat import HeartbeatManager
from .manager import ConnectionManager


logger = logging.getLogger(__name__)

# Router — auto-discovered by main.py via collect_objects(APIRouter)
router = APIRouter(tags=["websocket"])
router.add_api_websocket_route("/ws/{client_id}", websocket_endpoint)

# Module-level singletons (initialised by init_ws)
_manager: ConnectionManager | None = None
_heartbeat: HeartbeatManager | None = None


def get_manager() -> ConnectionManager | None:
    """Return the global ConnectionManager, or None if WS is not initialised."""
    return _manager


def get_heartbeat() -> HeartbeatManager | None:
    """Return the global HeartbeatManager, or None if WS is not initialised."""
    return _heartbeat


async def init_ws() -> tuple[ConnectionManager, HeartbeatManager]:
    """Initialise the WebSocket subsystem.

    Creates the ConnectionManager and HeartbeatManager.
    The HeartbeatManager is NOT started here — its ``run()``
    coroutine should be launched in the lifespan ``TaskGroup``.

    Also triggers import of ``app.ws.handlers`` to register the
    built-in message handlers (ping) and event handlers (ws_push).

    Returns:
        A tuple of (ConnectionManager, HeartbeatManager).
    """
    global _manager, _heartbeat

    _manager = ConnectionManager()
    _heartbeat = HeartbeatManager(_manager)

    # Import handlers to trigger @ws_message_handler and @event_handler registration
    from . import handlers as _handlers  # noqa: F811, F401

    logger.info(
        f"WebSocket server initialised "
        f"(heartbeat interval={_heartbeat.interval}s, "
        f"timeout={_heartbeat.timeout}s)"
    )
    return _manager, _heartbeat


async def shutdown_ws() -> None:
    """Shut down the WebSocket subsystem.

    Clears module-level references.  Active connections will be
    closed when the ASGI server shuts down.  The heartbeat reaper
    is stopped by cancelling its task in the ``TaskGroup``.
    """
    global _manager, _heartbeat

    _heartbeat = None
    _manager = None
    logger.info("WebSocket server shut down")
