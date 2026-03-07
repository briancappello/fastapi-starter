"""Typed event definitions for Kafka-sourced messages.

Each Kafka consumer feed can define its own Event subclass here.
When a Kafka message is received, the handler normalizes it into
one of these typed events and emits it to the transactional outbox.

This makes Kafka feeds first-class event sources — downstream handlers
receive the same typed events regardless of whether the data came from
Kafka, a REST endpoint, or any other source.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from app.events import Event


class FeedOneReceived(Event):
    """Event emitted when a feed-one Kafka message is received."""

    event_type: Literal["kafka.feed_one.received"] = "kafka.feed_one.received"
    event_name: str
    payload: dict | list


class FeedTwoReceived(Event):
    """Event emitted when a feed-two Kafka message is received."""

    event_type: Literal["kafka.feed_two.received"] = "kafka.feed_two.received"
    event_name: str
    payload: dict | list


class FeedThreeReceived(Event):
    """Event emitted when a feed-three Kafka message is received."""

    event_type: Literal["kafka.feed_three.received"] = "kafka.feed_three.received"
    event_name: str
    payload: dict | list
