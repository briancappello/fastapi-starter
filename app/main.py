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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # before startup
    try:
        await kafka_registry.start_all()
    except Exception:
        logger.warning(
            "Failed to connect to Kafka. "
            "Consumers will not start. "
            "Set KAFKA_ENABLED=false to suppress this warning.",
        )

    # Start event system components based on EVENT_MODE
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
        )
        await relay.start()

    if broker and Config.EVENT_MODE in ("all", "worker"):
        from .events.handlers import handler_registry
        from .events.worker import EventWorker

        # Discover handlers by importing the handlers package
        # (handler modules use @event_handler decorator which auto-registers)
        collect_objects(object, module_paths=["app.events.handlers"])

        groups = None
        if Config.EVENT_WORKER_GROUPS != "all":
            groups = {g.strip() for g in Config.EVENT_WORKER_GROUPS.split(",")}

        if handler_registry.get_groups():
            worker = EventWorker(
                broker=broker,
                session_factory=async_session_factory,
                groups=groups,
            )
            await worker.start()

    # begin serving requests
    yield

    # before shutdown
    if worker:
        await worker.stop()
    if relay:
        await relay.stop()
    if broker:
        await broker.stop()

    await kafka_registry.stop_all()


app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)

register_admin_views(app)
register_auth_views(app)

for router in collect_objects(
    APIRouter,
    module_paths=["app.views", "app.hello"],
    instances=True,
):
    app.include_router(router)
