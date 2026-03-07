# Event-Driven Architecture

## Overview

This system implements an event-driven architecture where all meaningful state changes are captured as **events** — immutable, typed records of things that happened. Events flow through a pipeline that decouples *producing* a change from *reacting* to it.

```
┌───────────────────────────────────────────────────────┐
│                   SOURCES                             │
├──────────┬───────────────────┬────────────────────────┤
│ Kafka    │ WebSocket inbound │ External webhooks, etc │
│ Consumer │ messages          │                        │
└────┬─────┴────────┬──────────┴───────────┬────────────┘
     │              │                      │
     ▼              ▼                      ▼
  ┌──────────────────────────────────────────┐
  │    Normalize → Write to Outbox           │  (event-first sources)
  └──────────────────┬───────────────────────┘
                     │
                     │    ┌──────────────────────────────────────┐
                     │    │         FastAPI Web Server           │
                     │    │  POST: write to app DB + outbox      │
                     │    │  GET: read from app DB               │
                     │    └───────────────┬──────────────────────┘
                     │                    │
                     ▼                    ▼
              ┌──────────────────────────────┐
              │       EVENT OUTBOX           │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────────┐
              │    Relay → RabbitMQ → Handlers   │
              │    (side effects, reactions)     │
              └──────────────────────────────────┘
```

## Data Flow

```
Source (API, Kafka, etc.)
  |
  v
[Domain Write + Event]  -- single DB transaction
  |
  v
Outbox Table (PostgreSQL)
  |
  v
Relay (LISTEN/NOTIFY + polling)
  |
  v
Message Broker (topic exchange)
  |
  +---> Handler Group A (queue A)
  |       |-> handler_a1(event, session)
  |       |-> handler_a2(event, session)
  |
  +---> Handler Group B (queue B)
          |-> handler_b1(event, session)
```

The architecture serves two goals:

1. **Reliability**: Domain mutations and their corresponding events are committed atomically. Events are never lost, even if downstream consumers are temporarily unavailable.
2. **Extensibility**: New reactions to existing events (sending emails, updating analytics, syncing to external systems) can be added without modifying the code that produces them.

## Core Concepts

### Events

An **event** is a typed, immutable record of something that happened in the system. Every event has:

- A globally unique ID
- A type string (e.g., `user.created`, `order.placed`)
- A timestamp
- A source identifier (where it came from)
- An optional correlation ID (for tracing causal chains)
- Domain-specific payload fields

Events are the system's lingua franca. They are the same regardless of whether they originated from an API call, a Kafka topic, a cron job, or a WebSocket message.

### Event Sources

Events enter the system from multiple sources:

- **Web server** — API endpoints that mutate state emit events in the same database transaction as the domain write.
- **Kafka** — External feeds are consumed, normalized into typed events, and written to the outbox.
- **Background jobs** — Scheduled or triggered tasks that produce events.
- **WebSocket / other protocols** — Any ingress point that produces meaningful state changes.

The key principle: **all sources produce the same event types**. A `user.created` event looks identical whether it came from a REST endpoint or a Kafka topic.

### Transactional Outbox

The **outbox** is a database table that acts as a durable, ordered log of events. When code emits an event, the event row is written to the outbox *in the same database transaction* as whatever domain operation produced it. This guarantees:

- If the domain write commits, the event is persisted.
- If the domain write rolls back, the event is discarded.
- No event is ever "lost in transit" between the application and the message broker.

The outbox is append-only from the application's perspective. A separate process reads from it and forwards events downstream.

### Relay

The **relay** is a background process that reads un-forwarded events from the outbox and publishes them to the message broker. It uses two mechanisms for low latency:

1. **Database change notifications** — The database notifies the relay immediately when a new event is inserted.
2. **Polling fallback** — A periodic sweep catches any events missed during relay downtime or notification failures.

The relay uses row-level locking so multiple relay instances can run concurrently without duplicating work.

### Message Broker

The **broker** is a topic-based message routing layer between the relay and the handlers. Events are published with their type as the routing key, and consumers bind to patterns they care about.

The broker provides:

- **Fan-out**: Multiple independent consumer groups can each receive every event.
- **Pattern matching**: Consumers subscribe to patterns (e.g., `user.*` matches `user.created` and `user.deleted`).
- **Durability**: Messages survive broker restarts.
- **Retry/dead-letter**: Failed messages are retried with backoff, then dead-lettered after max attempts.

### Handlers

A **handler** is an async function that reacts to events. Handlers are organized into **groups**. Each group maps to a broker queue — all handlers in the same group share work (competing consumers pattern).

Examples of handler groups:

- `notifications` — sends emails, push notifications
- `analytics` — updates dashboards, writes to data warehouse
- `search` — updates search indexes
- `projections` — maintains read-model tables

Handlers receive an event and a database session. They can do anything: update tables, call external APIs, emit new events.

### Exactly-Once Semantics

For **state-changing** handlers (updating a database row, incrementing a counter), the system provides exactly-once processing guarantees:

- Each handler group tracks which events it has already processed.
- The deduplication check and the handler's state change happen in the same database transaction.
- On redelivery, the dedup check prevents double-processing.

For **side-effect** handlers (sending emails, calling webhooks), the system provides at-least-once semantics. Idempotent design is recommended for these handlers.

## Deployment Modes

The system is designed to run as a **single process in development** and **split into separate processes in production**:

| Mode     | What runs                                  |
|----------|--------------------------------------------|
| `all`    | Web server + relay + workers (dev)         |
| `api`    | Web server only (outbox writes, no relay)  |
| `relay`  | Outbox relay only                          |
| `worker` | Event handler workers only                 |
| `kafka`  | Kafka consumers + outbox writes            |

In production, you might run:
- 2 `api` instances behind a load balancer
- 1 `relay` instance (with a hot standby)
- N `worker` instances (scaled per handler group)
- 1 `kafka` instance per partition assignment

## Design Principles

1. **Events are the source of truth for "what happened."** The outbox is an ordered, durable log. Even if the broker is down, events are safe in the database.

2. **Producers don't know about consumers.** The code that emits `order.placed` has no idea that a notification handler, an analytics handler, and a search indexer are all listening.

3. **Handlers are idempotent (or deduped).** The system may deliver an event more than once. State-changing handlers use deduplication; side-effect handlers should be designed to tolerate duplicates.

4. **The broker is a delivery mechanism, not a source of truth.** If the broker loses a message, the relay will re-publish from the outbox. The database is the durable store.

5. **Event types are explicit and typed.** Every event is a concrete class with a schema. There are no untyped JSON blobs floating through the system. The registry maps type strings to classes for deserialization.

6. **Failures are isolated.** A failing handler in one group does not block handlers in other groups. Dead-letter queues capture messages that exceed retry limits for manual inspection.
