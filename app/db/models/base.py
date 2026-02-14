import re

from datetime import datetime
from typing import Annotated

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declared_attr,
    mapped_column,
    relationship,
)
from sqlalchemy.orm import registry as SQLAlchemyRegistry


# types
pk = Annotated[int, mapped_column(primary_key=True)]


class Base(DeclarativeBase):
    __abstract__ = True

    registry = SQLAlchemyRegistry(
        metadata=MetaData(
            naming_convention={
                "ix": "ix_%(column_0_label)s",
                "uq": "uq_%(table_name)s_%(column_0_name)s",
                "ck": "ck_%(table_name)s_%(constraint_name)s",
                "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
                "pk": "pk_%(table_name)s",
            },
        ),
        type_annotation_map={
            datetime: TIMESTAMP(timezone=True),
            dict: JSONB,
            int: BigInteger,
            list: JSONB,
        },
    )

    @declared_attr
    def __tablename__(cls):
        # convert ClassName to snake_case
        s = re.sub(r"([a-z0-9])([A-Z])", "\\1_\\2", cls.__name__)
        return re.sub(r"([A-Z])([A-Z][a-z])", "\\1_\\2", s).lower()

    created_at: Mapped[datetime | None] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "Base",
    "ForeignKey",
    "Mapped",
    "mapped_column",
    "pk",
    "relationship",
]
