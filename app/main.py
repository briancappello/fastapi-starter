import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import ORJSONResponse

from .admin import register_admin_views
from .auth import register_auth_views
from .config import Config
from .db import async_session_factory
from .kafka import CONSUMER_CONFIGS, KafkaConsumerRegistry
from .utils import collect_objects


logger = logging.getLogger(__name__)


# Global registry for Kafka consumers
kafka_registry = KafkaConsumerRegistry(session_factory=async_session_factory)
for config in CONSUMER_CONFIGS:
    kafka_registry.register(config)

# Module-level references for health checks
_relay = None
_worker = None
_heartbeat = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager using structured concurrency.

    All background services (Kafka consumers, outbox relay, event
    worker, WebSocket heartbeat) run inside an ``asyncio.TaskGroup``.
    If any service crashes with an unhandled exception, the TaskGroup
    cancels all other services and the exception propagates, causing
    the process to exit so the orchestrator (systemd, k8s, etc.) can
    restart it.
    """
    global _relay, _worker, _heartbeat

    # Prepare event system components based on EVENT_MODE
    relay = None
    worker = None
    broker = None

    if Config.EVENT_MODE in ("all", "relay", "worker"):
        from .events.broker import RabbitMQBroker

        broker = RabbitMQBroker()
        try:
            await broker.start()
        except Exception:
            logger.warning(
                "Failed to connect to RabbitMQ. "
                "Event relay and workers will not start. "
                "Set EVENT_MODE=api to suppress this warning.",
                exc_info=True,
            )
            broker = None

    if broker and Config.EVENT_MODE in ("all", "relay"):
        from .events.relay import OutboxRelay

        relay = OutboxRelay(
            broker=broker,
            session_factory=async_session_factory,
            batch_size=Config.EVENT_RELAY_BATCH_SIZE,
            poll_interval=Config.EVENT_RELAY_POLL_INTERVAL,
            publish_timeout=Config.EVENT_RELAY_PUBLISH_TIMEOUT,
        )
        _relay = relay

    if broker and Config.EVENT_MODE in ("all", "worker"):
        from .events.handlers import handler_registry
        from .events.worker import EventWorker

        # Discover handlers by importing the handlers package
        collect_objects(object, module_paths=["app.events.handlers"])

        groups = None
        if Config.EVENT_WORKER_GROUPS != "all":
            groups = {g.strip() for g in Config.EVENT_WORKER_GROUPS.split(",")}

        if handler_registry.get_groups():
            worker = EventWorker(
                broker=broker,
                session_factory=async_session_factory,
                groups=groups,
                prefetch_count=Config.EVENT_WORKER_PREFETCH,
            )
            _worker = worker

    # Start WebSocket server if enabled
    heartbeat = None
    if Config.WS_ENABLED:
        from .ws import init_ws
        from .ws import router as ws_router

        manager, heartbeat = await init_ws()
        _heartbeat = heartbeat
        app.include_router(ws_router)
        collect_objects(object, module_paths=["app.ws.handlers"])

    # Run all background services inside a TaskGroup.
    # If any service crashes, all others are cancelled and the
    # process exits.
    try:
        async with asyncio.TaskGroup() as tg:
            # Kafka consumers
            try:
                await kafka_registry.run_all(tg)
            except Exception:
                logger.warning(
                    "Failed to connect to Kafka. "
                    "Consumers will not start. "
                    "Set KAFKA_ENABLED=false to suppress this warning.",
                )

            # Outbox relay
            if relay:
                tg.create_task(relay.run(), name="outbox-relay")

            # Event worker
            if worker:
                tg.create_task(worker.run(), name="event-worker")

            # WebSocket heartbeat
            if heartbeat:
                tg.create_task(heartbeat.run(), name="ws-heartbeat")

            # Yield control to FastAPI — serve HTTP requests
            # until shutdown is triggered.
            yield

            # Shutdown requested — cancel the TaskGroup by raising
            # CancelledError, which cancels all child tasks.
            raise asyncio.CancelledError()

    except* asyncio.CancelledError:
        # Expected during normal shutdown — TaskGroup cancels all
        # tasks when the lifespan exits.
        pass
    except* Exception as eg:
        # A background service crashed — log and re-raise so the
        # process exits.
        for exc in eg.exceptions:
            logger.error("Background service crashed", exc_info=exc)
        raise
    finally:
        # Clean up broker connection
        if broker:
            await broker.stop()

        # Clean up module-level references
        _relay = None
        _worker = None
        _heartbeat = None


app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)

register_admin_views(app)
register_auth_views(app)

for router in collect_objects(
    APIRouter,
    module_paths=["app.views", "app.hello"],
    instances=True,
):
    app.include_router(router)
