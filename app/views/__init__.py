from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import ORJSONResponse

from app.auth import require_user
from app.db import AsyncSession, get_async_session, select
from app.db.models import User
from app.schema import UserRead


root = APIRouter()
api_v1 = APIRouter(prefix="/api/v1", default_response_class=ORJSONResponse)


def register_views(app: FastAPI) -> None:
    app.include_router(root)
    app.include_router(api_v1)


@root.get("/")
async def index():
    return {"message": "Hello World!"}


@api_v1.get("/")
def api_index():
    return {"message": "Hello API!"}


@api_v1.get("/protected")
@require_user
def api_protected(user: User):
    return {"message": f"Hello {user.first_name} {user.last_name}!"}


@api_v1.get("/users", response_model=list[UserRead])
@require_user(is_superuser=True)
async def get_users(
    session: AsyncSession = Depends(get_async_session),
):
    return (await session.scalars(select(User))).all()
