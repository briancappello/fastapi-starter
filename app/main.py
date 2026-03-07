from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import ORJSONResponse

from .admin import register_admin_views
from .auth import register_auth_views
from .db import async_session_factory
from .kafka import CONSUMER_CONFIGS, KafkaConsumerRegistry
from .utils import collect_objects


# Global registry for Kafka consumers
kafka_registry = KafkaConsumerRegistry(session_factory=async_session_factory)
for config in CONSUMER_CONFIGS:
    kafka_registry.register(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # before startup
    await kafka_registry.start_all()

    # begin serving requests
    yield

    # before shutdown
    await kafka_registry.stop_all()


app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)

register_admin_views(app)
register_auth_views(app)

for router in collect_objects(
    APIRouter,
    module_paths=["app.views"],
    instances=True,
):
    app.include_router(router)
