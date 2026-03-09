"""Prometheus metrics endpoint.

Exposes all application metrics in the Prometheus text exposition format.
Designed to be scraped by Prometheus, Grafana Agent, or any compatible
collector.

Usage::

    GET /metrics

The DB pool gauges are updated on each request so values are always
fresh for the scraper.
"""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.db import update_pool_metrics


metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Return Prometheus metrics in text exposition format."""
    # Refresh DB pool gauges on each scrape
    update_pool_metrics()

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
