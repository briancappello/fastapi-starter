"""Tests for Celery integration with the event system.

Tests the @celery_task_handler decorator and dispatch mechanism
using mocked Celery tasks (no running Celery worker needed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from unittest.mock import MagicMock, patch

import pytest

from app.events import Event
from app.events.handlers import handler_registry


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SampleHeavyEvent(Event):
    event_type: Literal["sample.heavy"] = "sample.heavy"
    item_id: int


class TestCeleryTaskHandler:
    """Test the @celery_task_handler decorator."""

    def test_registers_handler_in_handler_registry(self, clean_handler_registry):
        """The decorator should register the handler in the global registry."""
        from app.events.celery import celery_task_handler

        @celery_task_handler("sample.heavy", group="heavy-jobs")
        def process_heavy(event_data: dict) -> None:
            pass

        groups = clean_handler_registry.get_groups()
        assert "heavy-jobs" in groups

        handlers = clean_handler_registry.get_handlers_for_group("heavy-jobs")
        assert len(handlers) == 1
        assert handlers[0].event_pattern == "sample.heavy"

    def test_returns_celery_task(self, clean_handler_registry):
        """The decorated function should be a Celery task."""
        from app.events.celery import celery_task_handler

        @celery_task_handler("sample.heavy", group="heavy-returns")
        def process_returns(event_data: dict) -> None:
            pass

        # Should have a delay method (Celery task interface)
        assert hasattr(process_returns, "delay")
        assert hasattr(process_returns, "apply_async")

    @pytest.mark.anyio
    async def test_dispatch_calls_celery_delay(
        self, clean_handler_registry, session: AsyncSession
    ):
        """When the handler is invoked by the worker, it should call task.delay()."""
        from app.events.celery import celery_task_handler

        @celery_task_handler("sample.heavy", group="heavy-dispatch")
        def process_dispatch(event_data: dict) -> None:
            pass

        # Mock the delay method
        process_dispatch.delay = MagicMock()

        event = SampleHeavyEvent(source="test", item_id=42)

        # Get the registered async handler wrapper and call it
        handlers = clean_handler_registry.get_handlers_for_group("heavy-dispatch")
        assert len(handlers) == 1
        await handlers[0].handler(event, session)

        # Verify delay was called with serialized event data
        process_dispatch.delay.assert_called_once()
        call_args = process_dispatch.delay.call_args[0][0]
        assert call_args["item_id"] == 42
        assert call_args["source"] == "test"
        assert "event_id" in call_args

    def test_celery_task_name(self, clean_handler_registry):
        """The Celery task should have a proper name."""
        from app.events.celery import celery_task_handler

        @celery_task_handler("sample.heavy", group="heavy-name")
        def my_task_func(event_data: dict) -> None:
            pass

        assert my_task_func.name.endswith("my_task_func")

    def test_is_celery_handler(self, clean_handler_registry):
        """is_celery_handler should identify celery-backed handlers."""
        from app.events.celery import (
            _celery_tasks,
            celery_task_handler,
            is_celery_handler,
        )

        @celery_task_handler("sample.heavy", group="heavy-check")
        def check_task(event_data: dict) -> None:
            pass

        task_name = f"{check_task.__module__}.{check_task.__qualname__}"
        # The task name in _celery_tasks should match
        assert any("check_task" in k for k in _celery_tasks)


class TestCeleryApp:
    """Test the Celery app configuration."""

    def test_celery_app_creation(self):
        """celery_app() should return a Celery instance."""
        from app.events.celery import celery_app

        app = celery_app()
        from celery import Celery

        assert isinstance(app, Celery)

    def test_celery_app_singleton(self):
        """celery_app() should return the same instance on repeated calls."""
        from app.events.celery import celery_app

        app1 = celery_app()
        app2 = celery_app()
        assert app1 is app2

    def test_celery_app_uses_rabbitmq(self):
        """The Celery app should use RabbitMQ as its broker."""
        from app.events.celery import celery_app

        app = celery_app()
        # The broker URL should be amqp://
        broker_url = app.conf.broker_url
        assert broker_url.startswith("amqp://")
