"""WebSocket connection manager.

Tracks active connections keyed by ``client_id`` (an opaque string
identifier, typically from the URL path).  A single client_id may have
multiple simultaneous connections.

The manager is a plain in-memory registry — when the process restarts,
clients reconnect.  When running multiple WS server instances behind a
load balancer, each instance has its own manager; fan-out is handled by
RabbitMQ delivering events to every instance's ``ws_push`` handler group.
"""

from __future__ import annotations

import asyncio
import logging
import time

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ConnectionState:
    """Per-connection state.

    Identity is by object instance (not by field values), since the
    same client_id may have multiple simultaneous connections stored
    in a ``set``.
    """

    client_id: str
    ws: WebSocket
    connected_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    subscriptions: set[str] = field(default_factory=set)
    authenticated: bool = False
    metadata: dict = field(default_factory=dict)

    def touch(self) -> None:
        """Update last_seen timestamp (called on any client activity)."""
        self.last_seen = time.monotonic()

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


class ConnectionManager:
    """In-memory registry of active WebSocket connections.

    Thread-safe for concurrent async access via a :class:`asyncio.Lock`.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[ConnectionState]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, client_id: str, ws: WebSocket) -> ConnectionState:
        """Register a new connection. Returns the ConnectionState."""
        state = ConnectionState(client_id=client_id, ws=ws)
        async with self._lock:
            if client_id not in self._connections:
                self._connections[client_id] = set()
            self._connections[client_id].add(state)
        logger.debug(
            f"WS connected: client_id={client_id} "
            f"(total connections for client: "
            f"{len(self._connections.get(client_id, set()))})"
        )
        return state

    async def disconnect(self, state: ConnectionState) -> None:
        """Remove a connection."""
        async with self._lock:
            conns = self._connections.get(state.client_id)
            if conns:
                conns.discard(state)
                if not conns:
                    del self._connections[state.client_id]
        logger.debug(f"WS disconnected: client_id={state.client_id}")

    async def send_to_client(self, client_id: str, data: dict) -> int:
        """Send a JSON message to all connections for a client_id.

        Returns the number of connections the message was sent to.
        Silently removes connections that fail (stale/closed).
        """
        async with self._lock:
            conns = self._connections.get(client_id)
            if not conns:
                return 0
            targets = list(conns)

        sent = 0
        stale: list[ConnectionState] = []
        for state in targets:
            try:
                await state.ws.send_json(data)
                sent += 1
            except Exception:
                stale.append(state)

        # Clean up stale connections
        if stale:
            async with self._lock:
                conns = self._connections.get(client_id)
                if conns:
                    for s in stale:
                        conns.discard(s)
                    if not conns:
                        del self._connections[client_id]

        return sent

    async def send_to_clients(self, client_ids: list[str], data: dict) -> int:
        """Send a JSON message to multiple clients. Returns total sent."""
        total = 0
        for client_id in client_ids:
            total += await self.send_to_client(client_id, data)
        return total

    async def broadcast(self, data: dict) -> int:
        """Send a JSON message to all connected clients. Returns total sent."""
        async with self._lock:
            all_client_ids = list(self._connections.keys())
        return await self.send_to_clients(all_client_ids, data)

    def get_connections(self, client_id: str) -> list[ConnectionState]:
        """Return a snapshot of connections for a client_id (non-async)."""
        conns = self._connections.get(client_id, set())
        return list(conns)

    @property
    def connection_count(self) -> int:
        """Total number of active connections across all clients."""
        return sum(len(conns) for conns in self._connections.values())

    @property
    def client_count(self) -> int:
        """Number of unique client_ids with active connections."""
        return len(self._connections)

    @property
    def client_ids(self) -> set[str]:
        """Set of all connected client_ids."""
        return set(self._connections.keys())
