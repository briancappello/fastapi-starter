from typing import TYPE_CHECKING

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy import String

from .base import Base, Mapped, mapped_column, pk, relationship


if TYPE_CHECKING:
    from .access_token import AccessToken


class User(SQLAlchemyBaseUserTable[int], Base):
    id: Mapped[pk]

    email: Mapped[str] = mapped_column(String(length=320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(length=1024))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    is_verified: Mapped[bool] = mapped_column(default=False)
    first_name: Mapped[str]
    last_name: Mapped[str]

    access_tokens: Mapped[list["AccessToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __str__(self):
        return self.email
