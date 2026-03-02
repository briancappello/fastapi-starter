"""Kafka health endpoints."""

from fastapi import APIRouter


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/kafka")
async def kafka_health():
    """Return status of all Kafka consumers."""
    from app.main import kafka_registry

    return {
        "enabled": True,
        "consumers": kafka_registry.get_status(),
    }
