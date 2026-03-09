# Kafka

Consumer-only Kafka integration that ingests external feeds, persists messages
for auditability, and normalizes them into typed events for the outbox pipeline.

## Data Flow

```
  Kafka topic                 Consumer                    PostgreSQL
  (external)                  (aiokafka)
  -----------                 ----------                  ----------

  "feed-one-topic"
       |
       v
  KafkaConsumerService
  (auto_commit=False)
       |
       v
  Parse JSON (orjson) + validate (KafkaMessageSchema)
       |
       v
  INSERT into kafka_message   ------>  kafka_message table
  ON CONFLICT DO NOTHING               (topic, partition, offset) UNIQUE
       |
       | (if not duplicate)
       v
  handler (e.g. handle_feed_one)
       |
       v
  emit_event(FeedOneReceived) ------>  event_outbox table
       |                               (picked up by OutboxRelay)
       v
  UPSERT kafka_offset         ------>  kafka_offset table
       |
       v
  COMMIT (all in one transaction)
```

## Key Design Decisions

- **Kafka is an ingress source, not the internal broker.** Messages are normalized
  into the same `Event` type hierarchy used by REST and WebSocket sources.
  Downstream handlers don't know or care where events originated.

- **DB-managed offsets.** Offsets are stored in PostgreSQL (`kafka_offset` table),
  not in Kafka consumer groups. The offset update commits in the same transaction
  as message storage and event emission, preventing duplicate processing.

- **Per-message transactions.** Each Kafka message is processed in its own DB
  transaction. If the handler fails, the offset is not advanced -- the message
  will be reprocessed on restart.

## Adding a New Consumer

### 1. Define the event type

```python
# app/kafka/events.py
from typing import Literal
from app.events import Event

class MyFeedReceived(Event):
    event_type: Literal["kafka.my_feed.received"] = "kafka.my_feed.received"
    event_name: str
    payload: dict | list
```

### 2. Write the handler

```python
# app/kafka/handlers.py
from app.events import emit_event
from .events import MyFeedReceived
from .schema import KafkaMessageSchema

async def handle_my_feed(message: KafkaMessageSchema, session):
    event = MyFeedReceived(
        event_name=message.event_name,
        payload=message.payload,
        source="kafka:my-feed",
    )
    await emit_event(event, session)
```

### 3. Register the consumer config

```python
# app/kafka/config.py
CONSUMER_CONFIGS.append(
    ConsumerConfig(
        name="my-feed",
        topics=["my-feed-topic"],
        schema=KafkaMessageSchema,
        handler=handle_my_feed,
    )
)
```

## Message Schema

All consumers currently use the same schema:

```python
class KafkaMessageSchema(BaseModel):
    event_name: str
    timestamp: datetime
    payload: dict | list
```

Custom schemas are supported -- set `schema` on `ConsumerConfig` to any Pydantic model.

## Database Tables

### kafka_message

Stores every received message for auditability and deduplication.

| Column | Type | Notes |
|---|---|---|
| `consumer_name` | String | indexed |
| `topic` | String | indexed |
| `partition` | int | |
| `offset` | int | |
| `event_name` | String | indexed |
| `timestamp` | datetime | from message |
| `payload` | JSONB | full payload |
| `received_at` | datetime | when consumed |

Unique constraint: `(topic, partition, offset)`

### kafka_offset

Tracks last-processed offset per consumer/topic/partition.

| Column | Type | Notes |
|---|---|---|
| `consumer_name` | String | indexed |
| `topic` | String | |
| `partition` | int | |
| `offset` | int | last committed offset |

Unique constraint: `(consumer_name, topic, partition)`

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package init, re-exports public API |
| `schema.py` | `KafkaMessageSchema` Pydantic model |
| `registry.py` | `ConsumerConfig` dataclass, `KafkaConsumerRegistry` lifecycle manager |
| `consumer.py` | `KafkaConsumerService` -- aiokafka wrapper with DB offsets |
| `events.py` | Typed event classes (`FeedOneReceived`, etc.) |
| `handlers.py` | Per-feed handlers that normalize to `emit_event()` |
| `config.py` | Static list of `ConsumerConfig` definitions |

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `KAFKA_ENABLED` | `true` | Enable/disable Kafka consumers |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker addresses |
| `KAFKA_CONSUMERS` | `all` | `all`, `none`, or comma-separated names |

## Running Standalone

```bash
# Run all consumers
app kafka run

# Run specific consumers
app kafka run feed-one,feed-two

# List registered consumers
app kafka list
```
