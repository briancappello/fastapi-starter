"""Application-level WebSocket heartbeat.

Provides tighter control over connection liveness than the protocol-level
WebSocket ping/pong (which uvicorn handles).  The heartbeat manager runs
a periodic reaper task that closes connections which haven't been seen
within the timeout window.

Clients must send ``{"type": "ping"}`` periodically.  The server
responds with ``{"type": "pong"}``.  If a client doesn't send anything
(including pings) within ``heartbeat_timeout`` seconds, the connection
is considered stale and is closed.
"""

from __future__ import annotations

import asyncio
import logging
import time

from typing import TYPE_CHECKING

from app.config import Config


if TYPE_CHECKING:
    from .manager import ConnectionManager

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """Manages application-level heartbeat for WebSocket connections.

    The reaper runs every ``interval`` seconds and closes any connection
    whose ``last_seen`` timestamp is older than ``timeout`` seconds.

    Args:
        manager: The ConnectionManager to monitor.
        interval: Seconds between reaper runs.
        timeout: Seconds of inactivity before a connection is reaped.
    """

    def __init__(
        self,
        manager: ConnectionManager,
        interval: float | None = None,
        timeout: float | None = None,
    ) -> None:
        self._manager = manager
        self._interval = (
            interval if interval is not None else Config.WS_HEARTBEAT_INTERVAL
        )
        self._timeout = timeout if timeout is not None else Config.WS_HEARTBEAT_TIMEOUT
        self._last_active: float = 0.0

    @property
    def last_active(self) -> float:
        """Monotonic timestamp of the last reaper loop iteration."""
        return self._last_active

    async def run(self) -> None:
        """Run the heartbeat reaper until cancelled.

        This is the primary entry point. Use inside an
        ``asyncio.TaskGroup`` for structured concurrency.
        """
        logger.info(
            f"WS heartbeat started (interval={self._interval}s, timeout={self._timeout}s)"
        )
        try:
            await self._reaper_loop()
        finally:
            logger.info("WS heartbeat stopped")

    async def start(self) -> None:
        """Start the reaper as a detached background task.

        Convenience wrapper for contexts that manage lifecycle
        manually (e.g. tests). Prefer :meth:`run` with a
        ``TaskGroup`` for production use.
        """
        self._task: asyncio.Task | None = asyncio.create_task(
            self.run(), name="ws-heartbeat-reaper"
        )

    async def stop(self) -> None:
        """Stop a reaper started with :meth:`start`."""
        task = getattr(self, "_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _reaper_loop(self) -> None:
        """Periodically check for stale connections and close them."""
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._reap_stale()
                self._last_active = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in heartbeat reaper loop")

    async def _reap_stale(self) -> None:
        """Close connections that haven't been seen within the timeout."""
        now = time.monotonic()
        threshold = now - self._timeout

        # Snapshot all connections
        stale = []
        for client_id in self._manager.client_ids:
            for conn in self._manager.get_connections(client_id):
                if conn.last_seen < threshold:
                    stale.append(conn)

        if not stale:
            return

        logger.info(f"Reaping {len(stale)} stale WS connection(s)")
        for conn in stale:
            try:
                await conn.ws.close(code=4008, reason="Heartbeat timeout")
            except Exception:
                pass  # Connection may already be closed
            await self._manager.disconnect(conn)

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def timeout(self) -> float:
        return self._timeout
