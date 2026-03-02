# Kafka Consumer Integration

This application includes a flexible Kafka consumer system that integrates with FastAPI's async lifecycle and SQLAlchemy for message persistence and offset management.

## Architecture Overview

- **Async-native**: Uses `aiokafka` for non-blocking Kafka consumption
- **DB-managed offsets**: Offsets stored in PostgreSQL instead of Kafka's internal storage
- **One transaction per message**: Atomic processing with automatic deduplication
- **Flexible deployment**: Run all consumers in one process (dev) or separate processes (prod)
- **Customizable schemas**: Each consumer can define its own Pydantic message schema

## Project Structure

```
app/
├── kafka/
│   ├── __init__.py      # Public exports
│   ├── schema.py        # Default KafkaMessageSchema
│   ├── consumer.py      # KafkaConsumerService
│   ├── registry.py      # ConsumerConfig + KafkaConsumerRegistry
│   ├── handlers.py      # Message handler functions
│   └── config.py        # Consumer definitions (feed-one, feed-two, feed-three)
├── db/models/
│   ├── kafka_offset.py  # Tracks offsets per topic/partition
│   └── kafka_message.py # Stores received messages
├── cli/
│   └── kafka.py         # CLI commands: `app kafka list`, `app kafka run`
└── views/
    └── kafka.py         # Health endpoint: GET /health/kafka
```

## Configuration

### Environment Variables

| Variable                  | Default          | Description                                                     |
|---------------------------|------------------|-----------------------------------------------------------------|
| `KAFKA_ENABLED`           | `true`           | Master switch to disable all consumers                          |
| `KAFKA_CONSUMERS`         | `all`            | Which consumers to run: `all`, `none`, or comma-separated names |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Default Kafka broker addresses                                  |

### ConsumerConfig

Each consumer is defined with a `ConsumerConfig`:

```python
from app.db import AsyncSession
from app.kafka import ConsumerConfig, KafkaMessageSchema


async def handle_feed_one(msg: KafkaMessageSchema, session: AsyncSession) -> None:
    pass


config = ConsumerConfig[KafkaMessageSchema](
    name="feed-one",                    # Unique identifier for CLI/health/filtering
    topics=["feed-one-topic"],          # Kafka topics to consume
    schema=KafkaMessageSchema,          # Pydantic model for parsing
    handler=handle_feed_one,            # Async function: (message, session) -> None
    bootstrap_servers=None,             # Specify to override global KAFKA_BOOTSTRAP_SERVERS
    group_id=None,                      # Specify to override default "{name}-consumer"
)
```

## Message Schema

### Default Schema

The default `KafkaMessageSchema` expects messages with:

```python
from datetime import datetime
from pydantic import BaseModel


class KafkaMessageSchema(BaseModel):
    event_name: str
    timestamp: datetime
    payload: dict | list
```

Example message:
```json
{
  "event_name": "user.created",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {"user_id": 123, "email": "user@example.com"}
}
```

### Custom Schemas

Each consumer can use a different schema:

```python
from pydantic import BaseModel

from app.db import AsyncSession
from app.kafka import ConsumerConfig


class UserEventSchema(BaseModel):
    user_id: int
    action: str
    metadata: dict

    
async def handle_user_events(msg: UserEventSchema, session: AsyncSession) -> None:
    pass


config = ConsumerConfig[UserEventSchema](
    name="user-events",
    topics=["user-events-topic"],
    schema=UserEventSchema,
    handler=handle_user_events,
)
```

When storing messages with custom schemas:
- `event_name`: Uses schema's `event_name` field if present, otherwise `"unknown"`
- `timestamp`: Uses schema's `timestamp` field if present, otherwise current time
- `payload`: Uses schema's `payload` field if present, otherwise `model.model_dump()`

## Database Models

### KafkaOffset

Tracks the last successfully processed offset per consumer/topic/partition:

```python
from sqlalchemy.orm import Mapped

from app.db.models.base import Base, pk


class KafkaOffset(Base):
    id: Mapped[pk]
    consumer_name: Mapped[str]  # e.g., "feed-one"
    topic: Mapped[str]
    partition: Mapped[int]
    offset: Mapped[int]
    # Unique constraint on (consumer_name, topic, partition)
```

### KafkaMessage

Stores all received messages:

```python
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Mapped

from app.db.models.base import Base, pk


class KafkaMessage(Base):
    id: Mapped[pk]
    consumer_name: Mapped[str]
    topic: Mapped[str]
    partition: Mapped[int]
    offset: Mapped[int]
    event_name: Mapped[str]
    timestamp: Mapped[datetime]
    payload: Mapped[Any]  # JSONB
    received_at: Mapped[datetime]
    # Unique constraint on (topic, partition, offset)
```

## Message Processing Flow

1. **Startup**: Consumer seeks to `DB offset + 1` for each partition (or `0` if no offset stored)
2. **Receive**: Message arrives from Kafka
3. **Parse**: Validate with Pydantic schema (malformed messages skip to step 6)
4. **Store**: Insert into `kafka_message` with `ON CONFLICT DO NOTHING`
5. **Handle**: If not a duplicate, call the handler function
6. **Commit**: Update offset in `kafka_offset` and commit transaction

This ensures:
- **Exactly-once semantics**: Duplicate messages are ignored via unique constraint
- **Resumability**: On restart, consumer picks up where it left off
- **Atomicity**: Message storage, handling, and offset update happen in one transaction

## Handlers

Handlers receive the parsed Pydantic model and a database session:

```python
from app.db import AsyncSession
from app.kafka.schema import KafkaMessageSchema

async def handle_feed_one(message: KafkaMessageSchema, session: AsyncSession) -> None:
    """Process feed-one messages."""
    logger.info(f"Processing event: {message.event_name}")
    
    # Access parsed fields
    if message.event_name == "user.created":
        user_data = message.payload
        # Do something with user_data...
    
    # Session is already in a transaction - just add operations
    # Commit happens automatically after handler returns
```

## Running Consumers

### Development (Single Process)

All consumers run alongside the FastAPI server:

```bash
# Default: all consumers enabled
uv run app dev

# Or explicitly
KAFKA_CONSUMERS=all uv run app dev
```

### Production (Separate Processes)

Run HTTP server and consumers in separate processes:

```bash
# Process 1: HTTP server only
KAFKA_CONSUMERS=none uv run uvicorn app:app --host 0.0.0.0 --port 8000

# Process 2: feed-one consumer
./run_consumer.sh feed-one

# Process 3: feed-two and feed-three consumers
./run_consumer.sh feed-two,feed-three
```

### CLI Commands

```bash
# List all registered consumers
uv run app kafka list

# Run specific consumers (standalone, no HTTP server)
uv run app kafka run feed-one
uv run app kafka run feed-one,feed-two
```

## Health Endpoint

`GET /health/kafka` returns consumer status:

```json
{
  "enabled": true,
  "consumers": {
    "feed-one": {
      "running": true,
      "topics": ["feed-one-topic"],
      "bootstrap_servers": "localhost:9092",
      "group_id": "feed-one-consumer"
    },
    "feed-two": {
      "running": true,
      "topics": ["feed-two-topic"],
      "bootstrap_servers": "localhost:9092",
      "group_id": "feed-two-consumer"
    }
  }
}
```

## Adding a New Consumer

1. **Define the handler** in `app/kafka/handlers.py`:

```python
async def handle_my_feed(message: MySchema, session: AsyncSession) -> None:
    logger.info(f"Processing: {message}")
```

2. **Add the config** in `app/kafka/config.py`:

```python
CONSUMER_CONFIGS: list[ConsumerConfig] = [
    # ... existing configs ...
    ConsumerConfig(
        name="my-feed",
        topics=["my-topic"],
        handler=handle_my_feed,
        schema=MySchema,  # Optional custom schema
    ),
]
```

3. **Run it**:

```bash
# In dev (runs with all others)
uv run app dev

# Or standalone
./run_consumer.sh my-feed
```

## Testing

Tests use mocked `AIOKafkaConsumer` - no real Kafka needed:

```bash
# Run kafka tests
KAFKA_ENABLED=false uv run pytest tests/kafka/ -v

# Run all tests
KAFKA_ENABLED=false uv run pytest -v
```

Key test coverage:
- Schema validation (default and custom)
- Message processing and storage
- Duplicate message handling
- Offset tracking and seeking
- Registry filtering (`KAFKA_CONSUMERS` env var)

## Error Handling

- **Malformed messages**: Logged, offset updated, processing continues
- **Handler exceptions**: Logged, offset NOT updated (message reprocessed on restart)
- **Duplicate messages**: Silently ignored via `ON CONFLICT DO NOTHING`

For production, consider adding:
- Dead letter queue for persistent failures
- Backpressure with `asyncio.Semaphore` for slow handlers
- Alerting on consumer lag
