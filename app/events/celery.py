"""Celery integration for event-driven architecture.

Provides a way to dispatch events to Celery tasks for heavy/long-running
processing. The EventWorker consumes from RabbitMQ and, for handlers
marked as Celery tasks, dispatches to Celery instead of running inline.

Architecture:
    1. Event is emitted to outbox
    2. Relay publishes to RabbitMQ
    3. EventWorker consumes the message
    4. For @celery_task_handler handlers, the worker calls task.delay()
       with the serialized event, then ACKs the RabbitMQ message
    5. Celery picks up the task and runs it with its own retry/visibility

Usage::

    from app.events.celery import celery_task_handler

    @celery_task_handler("order.placed", group="heavy-processing")
    def process_order(event_data: dict) -> None:
        # This runs as a Celery task (sync)
        order_id = event_data["order_id"]
        ...

The celery_task_handler decorator:
- Registers the function as an event handler (so the worker knows about it)
- Wraps it so the EventWorker dispatches to Celery instead of calling inline
- The Celery task receives the event as a plain dict (JSON-serializable)
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Awaitable, Callable

from .handlers import handler_registry


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .base import Event

logger = logging.getLogger(__name__)

# Registry of Celery task handlers: handler_name -> celery_task
_celery_tasks: dict[str, object] = {}


def get_celery_app():
    """Get or create the Celery application.

    Lazy-loaded to avoid import errors when Celery is not installed.
    """
    from app.config import Config

    try:
        from celery import Celery
    except ImportError:
        raise ImportError(
            "Celery is required for celery_task_handler. "
            "Install it with: uv pip install 'celery[amqp]'"
        )

    # Use the same RabbitMQ instance as the event system
    app = Celery(
        "events",
        broker=Config.RABBITMQ_URL,
        # Store results in RPC backend (returned via RabbitMQ)
        backend="rpc://",
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        # Prefix Celery queues to avoid collision with event system queues
        task_default_queue="celery.default",
    )
    return app


# Lazy singleton
_celery_app = None


def celery_app():
    """Return the shared Celery app instance."""
    global _celery_app
    if _celery_app is None:
        _celery_app = get_celery_app()
    return _celery_app


def celery_task_handler(
    event_pattern: str,
    group: str,
    **task_kwargs,
) -> Callable:
    """Decorator to register a function as a Celery-backed event handler.

    The decorated function becomes a Celery task that receives the event
    as a dict. The EventWorker dispatches to this task via task.delay()
    instead of calling the function inline.

    Args:
        event_pattern: Event type pattern (e.g., "order.placed").
        group: Handler group name.
        **task_kwargs: Additional arguments to pass to @app.task()
            (e.g., max_retries, rate_limit, queue).

    Example::

        @celery_task_handler("order.placed", group="fulfillment")
        def fulfill_order(event_data: dict) -> None:
            order_id = event_data["order_id"]
            # ... heavy processing ...

    Note: The function should be sync (Celery tasks are sync by default).
    If you need async, use Celery's async support or run an event loop.
    """

    def decorator(func: Callable) -> Callable:
        # Create the Celery task
        app = celery_app()
        task_name = f"{func.__module__}.{func.__qualname__}"
        task = app.task(name=task_name, bind=False, **task_kwargs)(func)

        # Store reference for lookup
        _celery_tasks[task_name] = task

        # Register an async wrapper as the event handler
        # The wrapper dispatches to Celery instead of running inline
        async def celery_dispatch(event: Event, session: AsyncSession):
            event_data = event.model_dump(mode="json")
            task.delay(event_data)
            logger.info(
                f"Dispatched {event.event_type} to Celery task "
                f"'{task_name}' (event_id={event.event_id})"
            )

        # Give the wrapper a meaningful name for the handler registry
        celery_dispatch.__module__ = func.__module__
        celery_dispatch.__qualname__ = func.__qualname__
        celery_dispatch.__name__ = func.__name__

        handler_registry.register(event_pattern, group, celery_dispatch)

        # Return the original Celery task (so it can be called directly)
        return task

    return decorator


def is_celery_handler(handler_name: str) -> bool:
    """Check if a handler name corresponds to a Celery task handler."""
    return handler_name in _celery_tasks
