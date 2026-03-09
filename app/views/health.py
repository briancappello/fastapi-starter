"""Health check endpoint for background services.

Reports the status of all background services (outbox relay, event
worker, Kafka consumers, WebSocket heartbeat) based on their
``last_active`` timestamps.  A service is considered healthy if its
last activity is within a configurable threshold.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from app.config import Config


router = APIRouter(prefix="/health", tags=["health"])

# A service is unhealthy if it hasn't been active within this many
# seconds.  For poll-based services (relay, heartbeat), this should
# be comfortably larger than their poll interval.
HEALTH_THRESHOLD_SECONDS = 60


def _service_status(
    name: str,
    last_active: float,
    *,
    threshold: float = HEALTH_THRESHOLD_SECONDS,
) -> dict:
    """Build a status dict for a single service."""
    now = time.monotonic()
    if last_active == 0.0:
        # Service hasn't completed its first iteration yet.
        # This is normal right after startup — don't mark unhealthy.
        return {
            "name": name,
            "status": "starting",
            "last_active_ago": None,
        }

    seconds_ago = round(now - last_active, 1)
    healthy = seconds_ago <= threshold
    return {
        "name": name,
        "status": "healthy" if healthy else "unhealthy",
        "last_active_ago": seconds_ago,
    }


@router.get("")
async def health():
    """Overall health check for all background services.

    Returns a JSON object with:
    - ``status``: ``"healthy"`` if all active services are healthy,
      ``"unhealthy"`` if any service has not been active within the
      threshold, ``"starting"`` if services are still initializing.
    - ``services``: Per-service status details.

    Services that are not enabled (e.g., Kafka when
    ``KAFKA_ENABLED=false``) are omitted from the response.
    """
    from app.main import _heartbeat, _relay, _worker, kafka_registry

    services = []

    # Outbox relay
    if _relay is not None:
        services.append(_service_status("outbox_relay", _relay.last_active))

    # Event worker
    if _worker is not None:
        services.append(_service_status("event_worker", _worker.last_active))

    # Kafka consumers
    for consumer in kafka_registry.consumers:
        services.append(
            _service_status(
                f"kafka:{consumer.config.name}",
                consumer.last_active,
            )
        )

    # WebSocket heartbeat
    if _heartbeat is not None:
        services.append(_service_status("ws_heartbeat", _heartbeat.last_active))

    # Determine overall status
    if not services:
        overall = "healthy"  # No background services to check
    elif any(s["status"] == "unhealthy" for s in services):
        overall = "unhealthy"
    elif any(s["status"] == "starting" for s in services):
        overall = "starting"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "services": services,
    }


@router.get("/kafka")
async def kafka_health():
    """Return status of all Kafka consumers."""
    from app.main import kafka_registry

    return {
        "enabled": Config.KAFKA_ENABLED,
        "consumers": kafka_registry.get_status(),
    }
