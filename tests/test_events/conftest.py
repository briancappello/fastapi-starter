"""Fixtures for event system tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.events.handlers import HandlerRegistry, handler_registry
from app.events.registry import EventRegistry, event_registry
from app.events.testing import InMemoryBroker


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def clean_event_registry():
    """Provide a clean event registry and restore the global one after.

    Since event types auto-register via __init_subclass__, we can't
    truly isolate the global registry. Instead, we save/restore it.
    """
    saved = event_registry._registry.copy()
    yield event_registry
    event_registry._registry = saved


@pytest.fixture
def clean_handler_registry():
    """Provide a clean handler registry and restore the global one after."""
    saved = handler_registry._handlers.copy()
    handler_registry._handlers = []
    yield handler_registry
    handler_registry._handlers = saved


@pytest.fixture
def in_memory_broker():
    """Provide an InMemoryBroker for tests."""
    return InMemoryBroker()


@pytest.fixture
def session_factory(session: AsyncSession):
    """Create a session factory that returns the test session.

    Matches the pattern used in test_kafka/conftest.py.
    """

    @asynccontextmanager
    async def get_session():
        yield session

    mock_factory = MagicMock()
    mock_factory.return_value = get_session()

    # Make it return a new context manager each time it's called
    def side_effect():
        return get_session()

    mock_factory.side_effect = side_effect

    return mock_factory
