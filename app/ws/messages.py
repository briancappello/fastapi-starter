"""WebSocket message routing.

Provides a decorator-based registry for inbound WebSocket message
handlers, analogous to ``@event_handler`` for the event system.

Inbound messages are freeform JSON with a ``type`` field that
determines dispatch::

    {"type": "order.submit", "symbol": "BTC", "qty": 1}
    {"type": "subscribe", "topic": "prices.BTC"}
    {"type": "ping"}

Handlers register for a specific message type and optionally
declare a Pydantic model for payload validation::

    @ws_message_handler("order.submit", model=OrderSubmitPayload)
    async def handle_order(payload, context):
        ...

    @ws_message_handler("subscribe")
    async def handle_subscribe(payload, context):
        ...

The ``payload`` argument is either the validated Pydantic model
instance (if ``model`` was specified) or the raw ``dict``.

The ``context`` argument is a :class:`MessageContext` containing
the connection state, session factory, and other useful references.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable


if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import models

    from .manager import ConnectionManager, ConnectionState

logger = logging.getLogger(__name__)


@dataclass
class MessageContext:
    """Context passed to every WebSocket message handler.

    Provides access to the connection, manager, DB session factory,
    and the authenticated user (if any).
    """

    conn: ConnectionState
    manager: ConnectionManager
    session_factory: async_sessionmaker[AsyncSession]
    user: models.User | None = None
    raw: dict | None = None  # the original raw message dict


# Type alias for handler functions.
# Receives (payload, context) where payload is dict or a Pydantic model instance.
WsMessageHandler = Callable[[Any, MessageContext], Awaitable[None]]


@dataclass
class MessageHandlerRegistration:
    """A registered WebSocket message handler."""

    message_type: str
    handler: WsMessageHandler
    model: type[BaseModel] | None  # optional Pydantic model for validation
    name: str  # fully qualified function name


class MessageRouter:
    """Registry and dispatcher for inbound WebSocket messages.

    Handlers are registered via :func:`ws_message_handler` decorator
    or :meth:`register`.  Messages are dispatched via :meth:`dispatch`.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandlerRegistration] = {}

    def register(
        self,
        message_type: str,
        handler: WsMessageHandler,
        model: type[BaseModel] | None = None,
    ) -> None:
        """Register a handler for a message type.

        Raises:
            ValueError: If a handler is already registered for this type.
        """
        if message_type in self._handlers:
            existing = self._handlers[message_type]
            raise ValueError(
                f"Duplicate ws_message_handler for type '{message_type}': "
                f"{existing.name} already registered"
            )

        name = (
            f"{getattr(handler, '__module__', '<unknown>')}"
            f".{getattr(handler, '__qualname__', getattr(handler, '__name__', repr(handler)))}"
        )
        self._handlers[message_type] = MessageHandlerRegistration(
            message_type=message_type,
            handler=handler,
            model=model,
            name=name,
        )
        logger.debug(f"Registered WS message handler {name} for '{message_type}'")

    async def dispatch(self, data: dict, context: MessageContext) -> bool:
        """Dispatch a message to the appropriate handler.

        Args:
            data: The raw JSON message (must have a ``"type"`` key).
            context: The message context.

        Returns:
            ``True`` if the message was handled, ``False`` if no handler
            matched (unknown type).
        """
        msg_type = data.get("type")
        if not msg_type or not isinstance(msg_type, str):
            logger.warning(
                f"WS message from client_id={context.conn.client_id} "
                f"missing or invalid 'type' field"
            )
            return False

        registration = self._handlers.get(msg_type)
        if registration is None:
            logger.debug(
                f"No handler for WS message type '{msg_type}' "
                f"from client_id={context.conn.client_id}"
            )
            return False

        # Parse payload through Pydantic model if specified
        if registration.model is not None:
            try:
                payload = registration.model.model_validate(data)
            except Exception as exc:
                logger.warning(
                    f"Validation failed for WS message type '{msg_type}' "
                    f"from client_id={context.conn.client_id}: {exc}"
                )
                # Send error back to client
                try:
                    await context.conn.ws.send_json(
                        {
                            "type": "error",
                            "error": "validation_error",
                            "message": str(exc),
                            "ref_type": msg_type,
                        }
                    )
                except Exception:
                    pass
                return True  # handled (as an error)
        else:
            payload = data

        # Store raw message on context for reference
        context.raw = data

        try:
            await registration.handler(payload, context)
        except Exception:
            logger.exception(
                f"Error in WS message handler '{registration.name}' "
                f"for type '{msg_type}' from client_id={context.conn.client_id}"
            )
            try:
                await context.conn.ws.send_json(
                    {
                        "type": "error",
                        "error": "internal_error",
                        "message": "An internal error occurred",
                        "ref_type": msg_type,
                    }
                )
            except Exception:
                pass

        return True

    def get_handler(self, message_type: str) -> MessageHandlerRegistration | None:
        """Look up a handler registration by message type."""
        return self._handlers.get(message_type)

    @property
    def message_types(self) -> set[str]:
        """All registered message types."""
        return set(self._handlers.keys())

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:
        types = sorted(self._handlers.keys())
        return f"MessageRouter(types={types})"


# Global singleton
message_router = MessageRouter()


def ws_message_handler(
    message_type: str,
    *,
    model: type[BaseModel] | None = None,
) -> Callable[[WsMessageHandler], WsMessageHandler]:
    """Decorator to register a function as a WebSocket message handler.

    Args:
        message_type: The ``"type"`` value in inbound messages that
            this handler processes (exact match).
        model: Optional Pydantic model class.  If provided, the raw
            message dict is validated through it before being passed
            to the handler as the ``payload`` argument.  If ``None``,
            the raw dict is passed directly.

    Example::

        @ws_message_handler("order.submit", model=OrderSubmitPayload)
        async def handle_order(payload: OrderSubmitPayload, ctx: MessageContext):
            event = OrderSubmitted(client_id=ctx.conn.client_id, ...)
            async with ctx.session_factory() as session:
                await emit_event(event, session)
                await session.commit()

        @ws_message_handler("subscribe")
        async def handle_subscribe(payload: dict, ctx: MessageContext):
            topic = payload.get("topic")
            ctx.conn.subscriptions.add(topic)
    """

    def decorator(func: WsMessageHandler) -> WsMessageHandler:
        message_router.register(message_type, func, model=model)
        return func

    return decorator
