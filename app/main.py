from contextlib import asynccontextmanager

from fastapi import FastAPI

from .admin import register_admins
from .auth import register_auth_views
from .views import register_views


@asynccontextmanager
async def lifecycle(app: FastAPI):
    # before startup
    # (no-op)

    # begin serving requests
    yield

    # before shutdown
    # (no-op)


app = FastAPI(life_cycle=lifecycle)

register_admins(app)
register_auth_views(app)
register_views(app)
