"""CLI commands for event system management."""

from __future__ import annotations

import asyncio
import signal

from . import click
from .groups import main


@main.group()
def events():
    """Event system management."""
    pass


@events.command("list")
def list_events():
    """List all registered event types and handlers."""
    from tabulate import tabulate

    from app.events.registry import event_registry
    from app.utils import collect_objects

    # Discover handlers by importing the handlers package
    collect_objects(object, module_paths=["app.events.handlers", "app.hello"])

    from app.events.handlers import handler_registry

    # Event types
    click.echo("Registered event types:")
    click.echo()
    event_types = sorted(event_registry.event_types)
    if event_types:
        rows = []
        for et in event_types:
            cls = event_registry.get(et)
            module = cls.__module__ if cls else "?"
            rows.append([et, f"{module}.{cls.__name__}" if cls else "?"])
        click.echo(
            tabulate(
                rows,
                headers=["Event Type", "Class"],
                tablefmt="simple",
            )
        )
    else:
        click.echo("  (none)")

    click.echo()

    # Handler groups
    click.echo("Registered handler groups:")
    click.echo()
    groups = sorted(handler_registry.get_groups())
    if groups:
        rows = []
        for group in groups:
            handlers = handler_registry.get_handlers_for_group(group)
            bindings = handler_registry.get_bindings_for_group(group)
            for h in handlers:
                rows.append([group, h.event_pattern, h.name])
        click.echo(
            tabulate(
                rows,
                headers=["Group", "Pattern", "Handler"],
                tablefmt="simple",
            )
        )
    else:
        click.echo("  (none)")


@events.command("relay")
async def run_relay():
    """Run the outbox relay (standalone mode, without HTTP server).

    Reads un-relayed events from the database outbox and publishes
    them to RabbitMQ. Uses PG LISTEN/NOTIFY for low-latency detection
    with a polling fallback.

    Example:
        app events relay
    """
    from app.db import async_session_factory
    from app.events.broker import RabbitMQBroker
    from app.events.relay import OutboxRelay

    click.echo("Starting outbox relay...")

    broker = RabbitMQBroker()
    try:
        await broker.start()
    except Exception as e:
        click.echo(f"Failed to connect to RabbitMQ: {e}", err=True)
        raise SystemExit(1)

    relay = OutboxRelay(
        broker=broker,
        session_factory=async_session_factory,
    )

    click.echo("Outbox relay running. Press Ctrl+C to stop.")

    # Set up signal-based cancellation
    shutdown_event = asyncio.Event()

    def signal_handler():
        click.echo("\nShutting down relay...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run the relay until signal received
    relay_task = asyncio.create_task(relay.run())

    # Wait for either shutdown signal or relay crash
    done, _ = await asyncio.wait(
        [relay_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If the relay task finished first, it crashed
    if relay_task in done:
        try:
            relay_task.result()  # Raises the exception
        except asyncio.CancelledError:
            pass
    else:
        # Shutdown signal — cancel the relay
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass

    await broker.stop()
    click.echo("Relay stopped.")


@events.command("worker")
@click.argument("groups", required=False)
async def run_worker(groups: str | None):
    """Run event handler workers (standalone mode, without HTTP server).

    GROUPS: Comma-separated list of handler groups to consume for.
            If not provided, consumes for all registered groups.

    Examples:
        app events worker                         # All groups
        app events worker notifications            # One group
        app events worker notifications,analytics  # Multiple groups
    """
    from app.db import async_session_factory
    from app.events.broker import RabbitMQBroker
    from app.events.handlers import handler_registry
    from app.events.worker import EventWorker
    from app.utils import collect_objects

    # Discover handlers
    collect_objects(object, module_paths=["app.events.handlers", "app.hello"])

    group_set = None
    if groups:
        group_set = {g.strip() for g in groups.split(",")}

    available_groups = handler_registry.get_groups()
    if not available_groups:
        click.echo("No handler groups registered.")
        return

    target_groups = group_set or available_groups
    click.echo(f"Starting worker for groups: {', '.join(sorted(target_groups))}")

    broker = RabbitMQBroker()
    try:
        await broker.start()
    except Exception as e:
        click.echo(f"Failed to connect to RabbitMQ: {e}", err=True)
        raise SystemExit(1)

    worker = EventWorker(
        broker=broker,
        session_factory=async_session_factory,
        groups=group_set,
    )

    click.echo("Worker running. Press Ctrl+C to stop.")

    # Set up signal-based cancellation
    shutdown_event = asyncio.Event()

    def signal_handler():
        click.echo("\nShutting down worker...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run the worker until signal received
    worker_task = asyncio.create_task(worker.run())

    # Wait for either shutdown signal or worker crash
    done, _ = await asyncio.wait(
        [worker_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If the worker task finished first, it crashed
    if worker_task in done:
        try:
            worker_task.result()  # Raises the exception
        except asyncio.CancelledError:
            pass
    else:
        # Shutdown signal — cancel the worker
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    await broker.stop()
    click.echo("Worker stopped.")
