from datetime import datetime
from typing import TYPE_CHECKING

from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTable
from sqlalchemy import ForeignKey, String, func

from .base import Base, Mapped, mapped_column, relationship


if TYPE_CHECKING:
    from .user import User


class AccessToken(SQLAlchemyBaseAccessTokenTable[int], Base):
    __tablename__ = "access_token"

    token: Mapped[str] = mapped_column(String(length=43), primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="cascade"))
    user: Mapped["User"] = relationship(back_populates="access_tokens")

    created_at: Mapped[datetime | None] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    def __str__(self):
        return self.token
