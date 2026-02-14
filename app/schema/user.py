from fastapi_users.schemas import BaseUser, BaseUserCreate, BaseUserUpdate


class UserRead(BaseUser[int]):
    first_name: str
    last_name: str


class UserCreate(BaseUserCreate):
    first_name: str
    last_name: str


class UserUpdate(BaseUserUpdate):
    first_name: str | None = None
    last_name: str | None = None
