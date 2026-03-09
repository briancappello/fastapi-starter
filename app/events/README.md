# Events

Transactional outbox event system with RabbitMQ broker, handler groups,
retry/DLQ, and optional Celery dispatch.

## Data Flow

```
  Domain code                     PostgreSQL                    RabbitMQ
  -----------                     ----------                    --------

  emit_event(event, session)
       |
       v
  INSERT into event_outbox --------> event_outbox table
  (same tx as domain write)           |
                                      | PG NOTIFY (instant)
                                      | + poll fallback (5s)
                                      v
                                 OutboxRelay
                                 SELECT ... FOR UPDATE SKIP LOCKED
                                 asyncio.gather(publish, publish, ...)
                                 bulk UPDATE relayed_at
                                      |
                                      v
                                 "events" topic exchange -----> handlers.{group} queue
                                                                     |
                                                                     v
                                                                EventWorker
                                                                deserialize -> run handlers -> ACK
                                                                     |
                                                            (on failure, retry via DLX)
                                                                     |
                                                            handlers.{group}.retry (TTL)
                                                                     |
                                                            (after max_retries)
                                                                     |
                                                            handlers.{group}.dlq
```

## Defining Events

Subclass `Event` and set `event_type`. Registration is automatic via `__init_subclass__`.

```python
from typing import ClassVar
from app.events import Event

class OrderPlaced(Event):
    event_type: ClassVar[str] = "order.placed"
    order_id: int
    total: float
```

Every event gets `event_id` (UUID), `timestamp`, `source`, and `correlation_id` automatically.

## Emitting Events

Call `emit_event()` inside an existing DB transaction. The event is written to the
outbox atomically with your domain write.

```python
from app.events import emit_event

async def place_order(order_data, session):
    order = Order(**order_data)
    session.add(order)
    await emit_event(
        OrderPlaced(order_id=order.id, total=order.total, source="rest:api_v1"),
        session,
    )
    await session.commit()  # order + event commit together
```

## Handling Events

Register handlers with `@event_handler`. The `group` determines the RabbitMQ queue --
handlers in the same group are competing consumers.

```python
from app.events.handlers import event_handler

@event_handler("order.placed", group="notifications")
async def send_order_confirmation(event, session):
    # This handler runs in the "notifications" worker group
    ...

@event_handler("order.*", group="analytics")
async def track_order_metrics(event, session):
    # Wildcard pattern -- matches order.placed, order.shipped, etc.
    ...
```

### Pattern Matching

AMQP topic patterns are supported:

| Pattern        | Matches                                        |
|----------------|------------------------------------------------|
| `order.placed` | Exact match only                               |
| `order.*`      | `order.placed`, `order.shipped` (one word)     |
| `order.#`      | `order.placed`, `order.item.added` (any depth) |
| `#`            | Everything                                     |

### Handler Groups

```
  Same group = competing consumers (load-balanced):
    @event_handler("order.*", group="notifications")  <-- only one gets each event
    @event_handler("order.*", group="notifications")

  Different groups = fan-out (each gets a copy):
    @event_handler("order.*", group="notifications")  <-- both get every event
    @event_handler("order.*", group="analytics")
```

## Celery Integration

For heavy or long-running work, use `@celery_task_handler` to dispatch to Celery
instead of running inline in the EventWorker.

```python
from app.events.celery import celery_task_handler

@celery_task_handler("report.requested", group="reports")
def generate_report(event_data: dict):
    # This runs as a Celery task (sync function)
    # The EventWorker calls task.delay() and ACKs immediately
    ...
```

## Testing

Use `InMemoryBroker` and test helpers to avoid RabbitMQ in tests:

```python
from app.events.testing import InMemoryBroker, assert_event_emitted, dispatch_to_handlers

# Verify an event was written to the outbox
await assert_event_emitted(session, "order.placed", count=1, order_id=42)

# Dispatch directly to handlers (bypasses broker)
await dispatch_to_handlers(event, session, groups={"notifications"})

# Capture published events
broker = InMemoryBroker()
await broker.publish(event)
assert len(broker.published) == 1
```

## RabbitMQ Topology

```
  Exchanges (topic, durable):
    events           routing_key = event_type
    events.retry     routing_key = group name
    events.dlx       routing_key = group name

  Per handler group:
    handlers.{group}         main queue (DLX -> events.retry)
    handlers.{group}.retry   TTL queue (DLX -> events, re-enters with original routing key)
    handlers.{group}.dlq     dead-letter queue (permanent failures)
```

## Database Tables

### event_outbox

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK | auto-increment |
| `event_id` | UUID | UNIQUE (idempotent writes) |
| `event_type` | String | indexed |
| `payload` | JSONB | full serialized event |
| `source` | String | origin identifier |
| `relayed_at` | Timestamp | NULL until relayed |

Partial index `ix_event_outbox_pending` on `id WHERE relayed_at IS NULL`.

PG trigger fires `NOTIFY event_outbox_channel` on INSERT.

### processed_event

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK | auto-increment |
| `event_id` | UUID | indexed |
| `handler_group` | String | indexed |

Unique constraint on `(event_id, handler_group)` for exactly-once processing.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Public API: `Event`, `emit_event`, `EventRegistry` |
| `base.py` | `Event` base model with auto-registration via `__init_subclass__` |
| `registry.py` | `EventRegistry` -- maps `event_type` strings to classes |
| `outbox.py` | `emit_event()` -- idempotent INSERT into outbox |
| `relay.py` | `OutboxRelay` -- PG LISTEN/NOTIFY + polling, batch relay to broker |
| `broker.py` | `EventBroker` protocol + `RabbitMQBroker` (dual-channel) |
| `handlers/__init__.py` | `@event_handler` decorator, `HandlerRegistry` |
| `worker.py` | `EventWorker` -- consumes from RabbitMQ, dispatches to handlers |
| `celery.py` | `@celery_task_handler` -- dispatches to Celery tasks |
| `testing.py` | `InMemoryBroker`, `assert_event_emitted`, `dispatch_to_handlers` |

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `EVENT_MODE` | `all` | `all`, `api`, `relay`, `worker` |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection |
| `EVENT_WORKER_GROUPS` | `all` | Which handler groups to consume |
