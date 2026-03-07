"""Tests for event system CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from app.cli.groups import main


class TestEventsListCommand:
    """Test 'app events list' command."""

    def test_list_shows_event_types(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "list"])
        assert result.exit_code == 0
        assert "Registered event types:" in result.output
        # HelloEvent should be discovered
        assert "hello.greeted" in result.output

    def test_list_shows_handler_groups(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "list"])
        assert result.exit_code == 0
        assert "Registered handler groups:" in result.output
        assert "hello" in result.output

    def test_list_shows_kafka_event_types(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "list"])
        assert result.exit_code == 0
        # Kafka events should be auto-registered on import
        assert "kafka.feed_one.received" in result.output


class TestEventsGroupHelp:
    """Test 'app events' help output."""

    def test_events_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "--help"])
        assert result.exit_code == 0
        assert "Event system management" in result.output

    def test_events_list_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "list", "--help"])
        assert result.exit_code == 0
        assert "List all registered event types" in result.output

    def test_events_relay_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "relay", "--help"])
        assert result.exit_code == 0
        assert "outbox relay" in result.output.lower()

    def test_events_worker_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["events", "worker", "--help"])
        assert result.exit_code == 0
        assert "event handler workers" in result.output.lower()
