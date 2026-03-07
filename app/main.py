from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from .admin import register_admin_views
from .auth import register_auth_views
from .utils import collect_objects


@asynccontextmanager
async def lifecycle(app: FastAPI):
    # before startup
    # (no-op)

    # begin serving requests
    yield

    # before shutdown
    # (no-op)


app = FastAPI(lifespan=lifecycle)

register_admin_views(app)
register_auth_views(app)

for router in collect_objects(
    APIRouter,
    module_paths=["app.views"],
    instances=True,
):
    app.include_router(router)
