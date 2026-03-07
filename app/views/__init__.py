from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse

from app.auth import require_user
from app.db import AsyncSession, async_session, select
from app.db.models import User
from app.schema import UserRead


root = APIRouter()
api_v1 = APIRouter(prefix="/api/v1", default_response_class=ORJSONResponse)


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
    session: AsyncSession = Depends(async_session),
):
    return (await session.scalars(select(User))).all()
