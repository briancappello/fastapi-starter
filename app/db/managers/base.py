from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Generic, TypeVar, get_args, get_origin

from sqlalchemy import select
from sqlalchemy.exc import StatementError as SQLAlchemyStatementError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.db.models.base import Base


ModelT = TypeVar("ModelT", bound=Base)


class ModelManager(Generic[ModelT]):
    """
    Base class for async model managers using SQLAlchemy 2.0 patterns.

    This is the preferred pattern for managing all interactions with the database
    for models when using an ORM that implements the Data Mapper pattern.

    You should create a subclass of ``ModelManager`` for every model in your app,
    customizing the :meth:`~ModelManager.create` method, and add any custom
    query methods you may need *here*, instead of on the model class.

    For example::

        from fin.db import AsyncSession
        from fin.db.model_manager import ModelManager
        from fin.db.models import User


        class UserManager(ModelManager[User]):

            async def create(
                self,
                name: str,
                email: str,
                commit: bool = False,
            ) -> User:
                return await super().create(
                    name=name,
                    email=email,
                    commit=commit,
                )

            async def find_by_email(self, email: str) -> User | None:
                return await self.get_by(email=email)


        # Usage:
        async with async_session_factory() as session:
            user_manager = UserManager(session)
            user = await user_manager.create(name="John", email="john@example.com")
    """

    model: type[ModelT]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Check if the user remembered to set cls.model
        if (
            (model := getattr(cls, "model", None))
            and isinstance(model, type)
            and issubclass(model, Base)
        ):
            return

        # Otherwise extract the model type from the generic parameter
        for base in cls.__orig_bases__:  # type: ignore[attr-defined]
            origin = get_origin(base)
            if origin is ModelManager or (
                isinstance(origin, type) and issubclass(origin, ModelManager)
            ):
                args = get_args(base)
                if args and isinstance(args[0], type) and issubclass(args[0], Base):
                    cls.model = args[0]  # noqa
                    break

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize the model manager with an async session.

        :param session: The async database session.
        """
        self.session = session

    def select(self) -> Select[tuple[ModelT]]:
        """
        Returns a SELECT statement for this manager's model.

        :return: A SQLAlchemy Select statement.
        """
        return select(self.model)

    async def create(self, commit: bool = False, **kwargs: Any) -> ModelT:
        """
        Creates an instance of ``self.model``, optionally committing the
        current session transaction.

        :param commit: Whether to commit the current session transaction.
        :param kwargs: The data to initialize the model with.
        :return: The created model instance.
        """
        instance = self.model(**kwargs)
        await self.save(instance, commit=commit)
        return instance

    async def _maybe_get_by(self, **kwargs: Any) -> ModelT | None:
        async with self.no_autoflush():
            try:
                return await self.get_by(**kwargs)
            except SQLAlchemyStatementError as e:
                if "no value has been set for this column" not in str(e):
                    raise e
        return None

    async def get_or_create(
        self,
        defaults: dict[str, Any] | None = None,
        commit: bool = False,
        **kwargs: Any,
    ) -> tuple[ModelT, bool]:
        """
        Get or create an instance of ``self.model`` by ``kwargs`` and
        ``defaults``, optionally committing the current session transaction.

        :param defaults: Extra values to create the model with, if not found.
        :param commit: Whether to commit the current session transaction.
        :param kwargs: The values to filter by and create the model with.
        :return: tuple[the_model_instance, did_create_bool]
        """
        instance = await self._maybe_get_by(**kwargs)
        if not instance:
            defaults = defaults or {}
            return await self.create(**defaults, **kwargs, commit=commit), True

        return instance, False

    async def update(
        self,
        instance: ModelT,
        commit: bool = False,
        **kwargs: Any,
    ) -> ModelT:
        """
        Update ``kwargs`` on an instance, optionally committing the current session
        transaction.

        :param instance: The model instance to update.
        :param commit: Whether to commit the current session transaction.
        :param kwargs: The data to update on the model.
        :return: The updated model instance.
        """
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self.save(instance, commit=commit)
        return instance

    async def update_or_create(
        self,
        defaults: dict[str, Any] | None = None,
        commit: bool = False,
        **kwargs: Any,
    ) -> tuple[ModelT, bool]:
        """
        Update or create an instance of ``self.model`` by ``kwargs`` and
        ``defaults``, optionally committing the current session transaction.

        :param defaults: Extra values to update on the model.
        :param commit: Whether to commit the current session transaction.
        :param kwargs: The values to filter by and update on the model.
        :return: tuple[the_model_instance, did_create_bool]
        """
        defaults = defaults or {}
        instance = await self._maybe_get_by(**kwargs)
        if not instance:
            return await self.create(**defaults, **kwargs, commit=commit), True

        for key, value in defaults.items():
            setattr(instance, key, value)
        return instance, False

    async def all(self) -> list[ModelT]:
        """
        Query the database for all records of ``self.model``.

        :return: A sequence of all model instances (can be empty).
        """
        result = await self.session.execute(self.select())
        return list(result.scalars().all())

    async def get(
        self,
        id: int | tuple[int, ...] | str | tuple[str, ...],
    ) -> ModelT | None:
        """
        Return an instance based on the given primary key identifier,
        or ``None`` if not found.

        E.g.::

            my_user = await user_manager.get(5)
            some_object = await some_manager.get((5, 10))

        :param id: A scalar or tuple value representing the primary key.
        :return: The object instance, or ``None``.
        """
        return await self.session.get(self.model, id)

    async def get_by(self, **kwargs: Any) -> ModelT | None:
        """
        Get one or none of ``self.model`` by ``kwargs``.

        :param kwargs: The data to filter by.
        :return: The model instance, or ``None``.
        """
        stmt = self.select().filter_by(**kwargs)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    def filter(self, *criterion: Any) -> Select[tuple[ModelT]]:
        """
        Get a SELECT statement for ``self.model`` matching ``criterion``.

        :param criterion: The criterion to filter by.
        :return: A SQLAlchemy Select statement.
        """
        return self.select().filter(*criterion)

    def filter_by(self, **kwargs: Any) -> Select[tuple[ModelT]]:
        """
        Get a SELECT statement for ``self.model`` matching ``kwargs``.

        :param kwargs: The data to filter by.
        :return: A SQLAlchemy Select statement.
        """
        return self.select().filter_by(**kwargs)

    async def save(self, instance: ModelT, commit: bool = False) -> ModelT:
        """
        Add a model instance to the session, optionally committing the current
        transaction immediately.

        :param instance: The model instance to save.
        :param commit: Whether to immediately commit.
        :return: The model instance.
        """
        self.session.add(instance)
        if commit:
            await self.session.commit()
        return instance

    async def save_all(
        self,
        instances: list[ModelT],
        commit: bool = False,
    ) -> list[ModelT]:
        """
        Adds a list of model instances to the session, optionally committing the
        current transaction immediately.

        :param instances: The list of model instances to save.
        :param commit: Whether to immediately commit.
        :return: The list of model instances.
        """
        self.session.add_all(instances)
        if commit:
            await self.session.commit()
        return instances

    async def delete(self, instance: ModelT, commit: bool = False) -> None:
        """
        Delete a model instance from the session, optionally committing the current
        transaction immediately.

        :param instance: The model instance to delete.
        :param commit: Whether to immediately commit.
        """
        await self.session.delete(instance)
        if commit:
            await self.session.commit()

    async def delete_all(
        self,
        instances: list[ModelT],
        commit: bool = False,
    ) -> None:
        """
        Delete a list of model instances from the session, optionally committing
        the current transaction immediately.

        :param instances: The list of model instances to delete.
        :param commit: Whether to immediately commit.
        """
        for instance in instances:
            await self.session.delete(instance)
        if commit:
            await self.session.commit()

    async def commit(self) -> None:
        """
        Commits the current transaction.
        """
        await self.session.commit()

    @asynccontextmanager
    async def no_autoflush(self) -> AsyncGenerator["ModelManager[ModelT]", None]:
        """
        Return an async context manager that disables autoflush.

        e.g.::

            async with manager.no_autoflush():
                some_object = SomeClass()
                manager.session.add(some_object)
                # won't autoflush
                some_object.related_thing = await manager.session.execute(
                    select(SomeRelated).limit(1)
                ).scalar()
        """
        autoflush = self.session.autoflush
        self.session.autoflush = False
        try:
            yield self
        finally:
            self.session.autoflush = autoflush


__all__ = [
    "ModelManager",
]
