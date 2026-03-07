"""CLI commands for Kafka consumer management."""

import asyncio
import signal

from . import click
from .groups import main


@main.group()
def kafka():
    """Kafka consumer management"""
    pass


@kafka.command("list")
def list_consumers():
    """List all registered Kafka consumers."""
    from app.kafka import CONSUMER_CONFIGS

    click.echo("Registered Kafka consumers:")
    click.echo()
    for config in CONSUMER_CONFIGS:
        click.echo(f"  {config.name}")
        click.echo(f"    topics: {', '.join(config.topics)}")
        click.echo(f"    group_id: {config.get_group_id()}")
        click.echo(f"    bootstrap_servers: {config.get_bootstrap_servers()}")
        click.echo()


@kafka.command("run")
@click.argument("consumers", required=False)
async def run_consumers(consumers: str | None):
    """Run Kafka consumers (standalone mode, without HTTP server).

    CONSUMERS: Comma-separated list of consumer names to run.
               If not provided, uses KAFKA_CONSUMERS env var.

    Examples:
        app kafka run                    # Run consumers from KAFKA_CONSUMERS env
        app kafka run feed-one           # Run only feed-one
        app kafka run feed-one,feed-two  # Run feed-one and feed-two
    """
    import os

    from app.config import Config
    from app.db import async_session_factory
    from app.kafka import CONSUMER_CONFIGS, KafkaConsumerRegistry

    # Override KAFKA_CONSUMERS if argument provided
    if consumers:
        os.environ["KAFKA_CONSUMERS"] = consumers
        # Force reload of config
        Config.KAFKA_CONSUMERS = consumers

    if not Config.KAFKA_ENABLED:
        click.echo("Kafka is disabled (KAFKA_ENABLED=false)")
        return

    # Create and populate registry
    registry = KafkaConsumerRegistry(session_factory=async_session_factory)
    for config in CONSUMER_CONFIGS:
        registry.register(config)

    # Track which consumers will run
    consumers_to_run = [c.name for c in CONSUMER_CONFIGS if registry._should_start(c)]

    if not consumers_to_run:
        click.echo("No consumers to run. Check KAFKA_CONSUMERS setting.")
        return

    click.echo(f"Starting consumers: {', '.join(consumers_to_run)}")

    # Setup signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler():
        click.echo("\nShutting down...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Start consumers
    await registry.start_all()
    click.echo("Consumers running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Stop consumers
    await registry.stop_all()
    click.echo("Consumers stopped.")
