# Event System - Quickstart

## Prerequisites

- Python 3.12+
- PostgreSQL (running, with a database created)
- RabbitMQ (optional for dev — the system gracefully degrades without it)

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

The defaults work for local development. Override via environment variables or `.env`:

```bash
# PostgreSQL (defaults shown)
SQL_DB_USER=fastapi
SQL_DB_PASSWORD=fastapi
SQL_DB_HOST=127.0.0.1
SQL_DB_PORT=5432
SQL_DB_NAME=fastapi

# RabbitMQ (optional — relay/worker won't start if unreachable)
RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Event system mode (see below)
EVENT_MODE=all
```

### 3. Run migrations

```bash
uv run alembic upgrade head
```

This creates all tables including `event_outbox`, `processed_event`, and the
PG NOTIFY trigger that wakes the relay on new events.

### 4. Start the server

```bash
uv run uvicorn app.main:app --reload
```

On startup you'll see the event system initialize. If RabbitMQ isn't available,
you'll get a warning but the web server still works — events accumulate in the
outbox and will be relayed once RabbitMQ comes up.

## EVENT_MODE

Controls which event subsystems run in the process:

| Value    | What starts                                |
|----------|--------------------------------------------|
| `all`    | Web server + relay + workers (dev default) |
| `api`    | Web server only (writes to outbox)         |
| `relay`  | Outbox relay only                          |
| `worker` | Event handler workers only                 |

In dev, `all` runs everything in one process. In production, you'd run
separate processes for each mode behind a process manager.

---

## Hello World: Define an Event + Write to Outbox

This walkthrough creates a new event type and emits it from an API endpoint.

### Step 1: Define the event

Create `app/events/definitions.py` (or any module — events auto-register on import):

```python
from typing import Literal

from app.events import Event


class UserGreeted(Event):
    """Fired when we greet a user."""

    event_type: Literal["user.greeted"] = "user.greeted"
    username: str
    message: str
```

That's it. The `Event` base class gives you `event_id`, `timestamp`, `source`,
and `correlation_id` for free. The `event_type` literal is how the system
identifies this event type everywhere — in the outbox, the broker, and handler
pattern matching.

Because `Event.__init_subclass__` auto-registers subclasses, importing this
module is all that's needed to make `"user.greeted"` known to the registry.

### Step 2: Emit the event from a route

Create `app/views/greet.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.events import emit_event

# Import so the event class is registered
from app.events.definitions import UserGreeted


router = APIRouter(prefix="/greet", tags=["greet"])


@router.post("/{username}")
async def greet_user(
    username: str,
    session: AsyncSession = Depends(async_session),
):
    # Create the event
    event = UserGreeted(
        source="rest:greet",
        username=username,
        message=f"Hello, {username}!",
    )

    # Write to the outbox IN the same DB transaction
    await emit_event(event, session)

    # Commit the transaction — the outbox row is now durable
    await session.commit()

    return {
        "event_id": str(event.event_id),
        "message": event.message,
    }
```

### Step 3: Try it

```bash
# Start the server
uv run uvicorn app.main:app --reload

# Hit the endpoint
curl -X POST http://localhost:8000/greet/world

# Response:
# {"event_id": "a1b2c3d4-...", "message": "Hello, world!"}
```

### Step 4: Verify the outbox

Connect to PostgreSQL and check:

```sql
SELECT event_id, event_type, payload, source, relayed_at
FROM event_outbox
ORDER BY created_at DESC
LIMIT 5;
```

You'll see your event with `relayed_at = NULL` (if RabbitMQ isn't running) or
a timestamp (if the relay already forwarded it).

---

## Hello World: Event Handler

This walkthrough creates a handler that reacts to the `user.greeted` event.

### Step 1: Create a handler module

Create `app/events/handlers/greet_handlers.py`:

```python
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.handlers import event_handler

# Import event class so it's registered
from app.events.definitions import UserGreeted


logger = logging.getLogger(__name__)


@event_handler("user.greeted", group="greetings")
async def log_greeting(event: UserGreeted, session: AsyncSession):
    """Log every greeting event."""
    logger.info(
        f"[greetings] {event.username} was greeted: {event.message} "
        f"(event_id={event.event_id})"
    )
```

The `@event_handler` decorator registers this function to receive events
matching `"user.greeted"` in the `"greetings"` handler group. The handler
module is auto-discovered at startup because `app/main.py` calls
`collect_objects(object, module_paths=["app.events.handlers"])`.

### Step 2: What happens at runtime

With `EVENT_MODE=all` and RabbitMQ running:

1. Your `POST /greet/world` writes the event to the outbox.
2. The PG NOTIFY trigger fires, waking the relay.
3. The relay reads the outbox row, publishes to RabbitMQ exchange `events`
   with routing key `user.greeted`.
4. The worker's `handlers.greetings` queue receives the message.
5. `log_greeting()` is called with the deserialized `UserGreeted` event
   and a fresh database session.

You'll see in logs:

```
INFO  [app.events.handlers.greet_handlers] [greetings] world was greeted: Hello, world! (event_id=a1b2c3d4-...)
```

### Step 3: Pattern matching

Handlers support AMQP-style topic patterns:

```python
# Exact match
@event_handler("user.greeted", group="greetings")

# Single-word wildcard: matches user.greeted, user.created, etc.
@event_handler("user.*", group="user-audit")

# Multi-word wildcard: matches everything
@event_handler("#", group="debug-logger")

# Prefix wildcard: matches order.created, order.item.added, etc.
@event_handler("order.#", group="order-processing")
```

---

## Hello World: Testing Events

The event system includes test helpers that work without RabbitMQ.

### Example test

```python
import pytest

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import Event, emit_event
from app.events.testing import assert_event_emitted, get_outbox_events


class ItemPurchased(Event):
    event_type: Literal["item.purchased"] = "item.purchased"
    item_id: int
    price: float


@pytest.mark.anyio
async def test_emit_purchase_event(session: AsyncSession):
    event = ItemPurchased(
        source="test",
        item_id=42,
        price=9.99,
    )

    await emit_event(event, session)
    await session.flush()

    # Option A: query outbox directly
    events = await get_outbox_events(session, "item.purchased")
    assert len(events) == 1
    assert events[0].payload["item_id"] == 42

    # Option B: use the assertion helper
    await assert_event_emitted(
        session,
        "item.purchased",
        count=1,
        item_id=42,
        price=9.99,
    )
```

### Testing handlers with InMemoryBroker

```python
import pytest

from app.events.testing import InMemoryBroker
from app.events.relay import OutboxRelay


@pytest.mark.anyio
async def test_event_flows_through_relay(
    session,
    session_factory,  # from tests/test_events/conftest.py
):
    broker = InMemoryBroker()
    relay = OutboxRelay(broker=broker, session_factory=session_factory)

    event = ItemPurchased(source="test", item_id=1, price=5.00)
    await emit_event(event, session)
    await session.commit()

    relayed = await relay.relay_once()
    assert relayed == 1
    assert len(broker.published) == 1
    assert broker.published[0].event_type == "item.purchased"
```

### Running tests

```bash
# All tests
uv run pytest

# Just event tests
uv run pytest tests/test_events/ -v
```

---

## File Layout

```
app/events/
  __init__.py          # Public API: Event, emit_event, event_registry
  base.py              # Event base class (Pydantic) + auto-registration
  registry.py          # EventRegistry: type string -> class mapping
  outbox.py            # emit_event() — writes to outbox table
  broker.py            # EventBroker protocol + RabbitMQBroker
  relay.py             # OutboxRelay: DB -> broker bridge
  worker.py            # EventWorker: broker -> handler dispatcher
  testing.py           # InMemoryBroker, get_outbox_events, assert_event_emitted
  handlers/
    __init__.py        # @event_handler decorator + HandlerRegistry

app/db/models/
  event_outbox.py      # EventOutbox SQLAlchemy model
  processed_event.py   # ProcessedEvent model (handler dedup)

migrations/versions/
  0ec60bc5c0c2_...py   # Migration: event tables + PG NOTIFY trigger

tests/test_events/
  conftest.py          # Fixtures: clean registries, InMemoryBroker, session_factory
  test_event_models.py # Event base model tests
  test_registry.py     # Registry lookup/deserialization tests
  test_outbox.py       # emit_event + outbox model tests
  test_handlers.py     # Handler registration + pattern matching tests
  test_relay.py        # OutboxRelay + InMemoryBroker tests
  test_integration.py  # End-to-end flow + dedup + testing helper tests
```

---

## Quick Reference

### Defining an event

```python
from typing import Literal
from app.events import Event

class OrderPlaced(Event):
    event_type: Literal["order.placed"] = "order.placed"
    order_id: int
    total: float
```

### Emitting an event

```python
from app.events import emit_event

await emit_event(order_event, session)
await session.commit()  # or let the caller commit
```

### Handling an event

```python
from app.events.handlers import event_handler

@event_handler("order.placed", group="fulfillment")
async def start_fulfillment(event, session):
    ...
```

### Checking the outbox in tests

```python
from app.events.testing import assert_event_emitted

await assert_event_emitted(session, "order.placed", count=1, order_id=42)
```
