"""WebSocket endpoint: /ws/{client_id}

Handles the full lifecycle of a WebSocket connection:
1. Accept the upgrade
2. Authenticate (based on WS_AUTH_METHOD)
3. Register with ConnectionManager
4. Emit a ``ws.client.connected`` event
5. Enter receive loop — dispatch messages via MessageRouter
6. On disconnect: emit ``ws.client.disconnected``, unregister
"""

from __future__ import annotations

import logging
import time

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.config import Config


logger = logging.getLogger(__name__)


async def websocket_endpoint(
    ws: WebSocket,
    client_id: str,
) -> None:
    """Main WebSocket endpoint handler.

    This is called by the router in ``__init__.py``.  All dependencies
    (manager, authenticator, message_router, session_factory) are
    accessed via the package-level singletons.
    """
    from app.db import async_session_factory
    from app.events.outbox import emit_event

    from . import get_manager
    from .auth import create_authenticator
    from .events import WsClientConnected, WsClientDisconnected
    from .messages import MessageContext, message_router

    manager = get_manager()
    if manager is None:
        await ws.close(code=4503, reason="WebSocket server not available")
        return

    # Accept the connection
    await ws.accept()

    # Authenticate
    authenticator = create_authenticator()
    user = await authenticator.authenticate(ws, client_id=client_id)

    auth_required = Config.WS_AUTH_METHOD != "none"
    if auth_required and user is None:
        await ws.send_json(
            {
                "type": "error",
                "error": "auth_failed",
                "message": "Authentication failed",
            }
        )
        await ws.close(code=4001, reason="Authentication failed")
        return

    # Register connection
    conn = await manager.connect(client_id, ws)
    conn.authenticated = user is not None
    if user is not None:
        conn.metadata["user_id"] = user.id  # type: ignore[union-attr]
        conn.metadata["email"] = user.email  # type: ignore[union-attr]

    connect_time = time.monotonic()

    # Emit connected event
    try:
        async with async_session_factory() as session:
            await emit_event(
                WsClientConnected(
                    client_id=client_id,
                    source="ws",
                ),
                session,
            )
            await session.commit()
    except Exception:
        logger.exception(f"Failed to emit WsClientConnected for {client_id}")

    # Send welcome message
    await ws.send_json(
        {
            "type": "connected",
            "client_id": client_id,
            "authenticated": conn.authenticated,
        }
    )

    # Build message context
    context = MessageContext(
        conn=conn,
        manager=manager,
        session_factory=async_session_factory,
        user=user,
    )

    # Receive loop
    try:
        while True:
            data = await ws.receive_json()
            conn.touch()

            # Dispatch through message router
            handled = await message_router.dispatch(data, context)
            if not handled:
                msg_type = data.get("type", "<missing>")
                await ws.send_json(
                    {
                        "type": "error",
                        "error": "unknown_type",
                        "message": f"Unknown message type: {msg_type}",
                        "ref_type": msg_type,
                    }
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(f"Error in WS receive loop for client_id={client_id}")
    finally:
        # Disconnect
        duration = time.monotonic() - connect_time
        await manager.disconnect(conn)

        try:
            async with async_session_factory() as session:
                await emit_event(
                    WsClientDisconnected(
                        client_id=client_id,
                        source="ws",
                        duration_seconds=round(duration, 2),
                    ),
                    session,
                )
                await session.commit()
        except Exception:
            logger.exception(f"Failed to emit WsClientDisconnected for {client_id}")
