# Event-Driven Architecture: Design Decisions

## System Overview

```
 INGRESS                          CORE PIPELINE                             EGRESS
 -------                          -------------                             ------

 +----------+
 | REST API |---+
 +----------+   |
                |    +------------+     +---------+     +----------+     +----------+     +--------------+
 +----------+   +--->|            |     |         |     |  Outbox  |     |          |---->| Handlers     |
 | Kafka    |------->| emit_event |---->| Outbox  |---->|  Relay   |---->| RabbitMQ |     | (inline)     |
 | Consumer |   +--->| (in DB tx) |     | (PG)    |     |          |     | (topic)  |     +--------------+
 +----------+   |    +------------+     +---------+     +----------+     +----------+     +--------------+
                |                           |                                |----------->| Celery       |
 +----------+   |                      PG NOTIFY                             |            | (async)      |
 | WebSocket|---+                     (wakes relay)                          |            +--------------+
 | Inbound  |                                                                |            +--------------+
 +----------+                                                                +----------->| WS Push      |
                                                                                          | (to clients) |
                                                                                          +--------------+
```

---

## Decision 1: Transactional Outbox vs Direct Publish

**Chosen: Transactional Outbox (PostgreSQL table)**

The event is written to a DB table in the same transaction as the domain write.
A separate relay process reads pending rows and publishes to RabbitMQ.

```
# What happens on POST /hello:
async with session.begin():
    session.add(Greeting(name=name))       # domain write
    await emit_event(HelloEvent(...), session)  # outbox INSERT
    # both commit or both rollback -- atomic
```

|                  | Transactional Outbox                            | Direct Publish to RabbitMQ                           |
|------------------|-------------------------------------------------|------------------------------------------------------|
| **Atomicity**    | Domain write + event are one transaction        | Two-phase: DB commit then broker publish can diverge |
| **Ghost events** | Impossible -- if DB rolls back, no row exists   | Possible -- published but domain write failed        |
| **Lost events**  | Impossible -- row persists until relay confirms | Possible -- publish fails after DB commit            |
| **Latency**      | +1-5ms (relay poll/NOTIFY)                      | Near-zero (direct publish)                           |
| **Complexity**   | Relay process, outbox table, cleanup            | Simpler initially, harder to debug failures          |
| **Throughput**   | Bounded by DB write speed                       | Bounded by broker                                    |

**Why this over alternatives:**
- **CDC (Change Data Capture)** via Debezium/WAL: More operationally complex, requires additional infrastructure. Outbox is self-contained within the app DB.
- **Dual writes with saga**: Eventual consistency is harder to reason about; partial failures require compensation logic.

---

## Decision 2: Event Relay -- LISTEN/NOTIFY + Polling Fallback

**Chosen: PG LISTEN/NOTIFY for instant wake-up, fixed-interval polling as safety net**

```
                         +------- PG NOTIFY (instant) -------+
                         |                                   |
  INSERT into outbox --> PG                                  |
                         ^                                   v
                         |                            +------+------+
                         +---- polls every 5s--------<| Relay Loop  |
                                                      +-------------+
```

|                    | LISTEN/NOTIFY + Poll                               | Pure Polling        | WAL-based CDC            |
|--------------------|----------------------------------------------------|---------------------|--------------------------|
| **Latency**        | ~instant (NOTIFY)                                  | Up to poll interval | ~instant                 |
| **Reliability**    | NOTIFY can be missed on reconnect; poll catches up | Always reliable     | Reliable                 |
| **Infrastructure** | Just PostgreSQL                                    | Just PostgreSQL     | Debezium + Kafka Connect |
| **Complexity**     | Moderate (raw asyncpg for LISTEN)                  | Simple              | High                     |

The hybrid approach gets sub-millisecond latency in the happy path with guaranteed delivery via the polling fallback. The raw `asyncpg` connection for LISTEN is separate from the SQLAlchemy pool.

---

## Decision 3: SELECT FOR UPDATE SKIP LOCKED for Relay Concurrency

**Chosen: Row-level locking with SKIP LOCKED**

```sql
SELECT * FROM event_outbox
WHERE relayed_at IS NULL
ORDER BY id
LIMIT :batch_size
FOR UPDATE SKIP LOCKED
```

This allows multiple relay instances to run concurrently without processing the same rows. Each instance grabs the next available batch. Combined with a partial index on `relayed_at IS NULL`, the query is fast even with millions of rows.

**Alternative considered**: Single relay instance with advisory locks. Simpler but doesn't scale horizontally.

---

## Decision 4: RabbitMQ Topology -- Topic Exchange + Handler Groups

**Chosen: Single topic exchange, one queue per handler group**

```
                   events (topic exchange)
                   routing_key = event_type
                              |
              +---------------+----------------+
              |               |                |
              v               v                v
     handlers.hello     handlers.ws_push   handlers.analytics
     (durable queue)    (durable queue)    (durable queue)
     binding: hello.#   binding: ws.#      binding: #
              |               |                |
         competing        competing        competing
         consumers        consumers        consumers
```

- **Same group** = competing consumers (load-balanced, only one gets each message)
- **Different groups** = fan-out (each group gets its own copy)
- **Topic patterns**: `hello.#`, `ws.*`, `order.created` -- AMQP wildcard matching

|                 | Topic Exchange + Groups                 | Direct Exchange per Handler | Fanout + Filtering          |
|-----------------|-----------------------------------------|-----------------------------|-----------------------------|
| **Flexibility** | Wildcard patterns, easy to add handlers | Exact routing only          | All handlers see all events |
| **Fan-out**     | Natural via multiple queue bindings     | Requires multiple exchanges | Natural but wasteful        |
| **Scaling**     | Add consumers to a group                | Add consumers per queue     | Same                        |
| **Filtering**   | Broker-side via routing key             | Broker-side                 | Application-side (wasteful) |

---

## Decision 5: Dual-Channel RabbitMQ Broker

**Chosen: Separate publish channel (no confirms) + consumer channel (with confirms)**

```
  RabbitMQBroker
  +--------------------------------------------------+
  |                                                    |
  |  _channel (main)          _publish_channel         |
  |  - publisher_confirms=ON  - publisher_confirms=OFF |
  |  - used by: worker        - used by: relay         |
  |    (queue declare,          (fire-and-forget        |
  |     consume, ACK/NACK)       event publish)         |
  |                                                    |
  +--------------------------------------------------+
```

**Rationale**: The outbox already guarantees durability. If a publish is dropped, the relay will retry from the outbox on the next batch. Publisher confirms add ~2-5x latency per publish and were the #1 throughput bottleneck in benchmarks.

**Benchmark impact** (this was one of three relay optimizations):

| Optimization                                   | Before            | After                      |
|------------------------------------------------|-------------------|----------------------------|
| Disable publisher confirms on relay channel    | baseline          | ~2x throughput             |
| Bulk UPDATE for `relayed_at` (single SQL vs N) | --                | +30%                       |
| Concurrent publish via `asyncio.gather`        | --                | +2x                        |
| **Combined**                                   | **~800 events/s** | **~6,680 events/s (8.3x)** |

---

## Decision 6: Handler Retries -- DLX Retry Queue + Celery for Heavy Work

**Chosen: Hybrid approach**

```
  Simple handlers:                      Complex handlers:
  RabbitMQ retry via DLX                Celery task dispatch

  handlers.hello (queue)                handlers.reports (queue)
       |                                     |
       v  (handler fails)                    v
  handlers.hello.retry                  @celery_task_handler
  (TTL=5s, DLX back                    calls task.delay(event_data)
   to main exchange)                    ACKs the RabbitMQ message
       |                                     |
       v  (after max_retries=5)              v
  handlers.hello.dlq                    Celery worker
  (dead letter queue)                   (own retry/visibility)
```

|                        | DLX Retry Only         | Celery Only           | Hybrid           |
|------------------------|------------------------|-----------------------|------------------|
| **Simple handlers**    | Fast, no extra infra   | Overkill overhead     | DLX retry (fast) |
| **Heavy/long-running** | Blocks consumer        | Natural fit           | Celery dispatch  |
| **Retry control**      | TTL + max count        | Celery retry policies | Best of both     |
| **Observability**      | RabbitMQ management UI | Celery Flower         | Both             |

**Design rule**: State changes need exactly-once guarantees (idempotent handlers + outbox). Side effects (emails, WS pushes) can be at-least-once.

---

## Decision 7: Exactly-Once Semantics via Processed Events Table

```
  EventWorker.process_message:
  1. Deserialize event from RabbitMQ message
  2. Check ProcessedEvent table (event_id + group)
  3. If already processed -> ACK and skip
  4. Run handlers within DB session
  5. INSERT into ProcessedEvent (event_id, group)
  6. Commit
  7. ACK
```

The `ProcessedEvent` table acts as an idempotency key. If the worker crashes after step 6 but before step 7, the message will be redelivered, but step 3 catches the duplicate.

**Alternative**: Rely on RabbitMQ message deduplication plugins. These don't survive broker restarts and have TTL limitations.

---

## Decision 8: Event Type Auto-Registration via `__init_subclass__`

**Chosen: Automatic registration when a subclass defines `event_type`**

```python
class HelloEvent(Event):
    event_type: ClassVar[str] = "hello.greeted"  # auto-registered
    name: str
    greeting: str

# No manual registry.register() call needed
# EventRegistry.get("hello.greeted") -> HelloEvent
```

|                         | `__init_subclass__` Auto-Register | Manual Registry               | Metaclass       |
|-------------------------|-----------------------------------|-------------------------------|-----------------|
| **Boilerplate**         | Zero                              | One extra call per event type | Zero            |
| **Discoverability**     | Import triggers registration      | Explicit but verbose          | Import triggers |
| **Debuggability**       | Implicit -- harder to trace       | Explicit -- easy to trace     | Implicit        |
| **Duplicate detection** | Raises ValueError on conflict     | Same                          | Same            |

Tradeoff accepted: the implicit behavior requires that event modules are imported before deserialization. The `collect_objects()` utility handles this by importing handler modules at startup.

---

## Decision 9: Kafka as Ingress Source (Not Broker)

**Chosen: Kafka messages are normalized into outbox events**

```
  Kafka topic          Kafka consumer         Outbox
  "feed-one"  -------> handler normalizes --> emit_event(FeedOneReceived(...))
                        to typed Event
```

Kafka is not used as the internal event broker. It's treated as an external data source, like a REST API webhook.

|                      | Kafka as Source + RabbitMQ Internal         | Kafka for Everything           | RabbitMQ for Everything              |
|----------------------|---------------------------------------------|--------------------------------|--------------------------------------|
| **Internal routing** | RabbitMQ topic exchange (flexible patterns) | Kafka topics (partition-based) | RabbitMQ (same)                      |
| **Consumer groups**  | RabbitMQ competing consumers                | Kafka consumer groups          | Same                                 |
| **Retry/DLQ**        | RabbitMQ DLX (built-in)                     | Manual (no native DLQ)         | Same                                 |
| **External feeds**   | Kafka consumer normalized to outbox         | Direct consumption             | Would need Kafka-to-RabbitMQ bridge  |
| **Operational cost** | Two brokers                                 | One broker                     | Need Kafka bridge for external feeds |

**Why**: RabbitMQ's topic exchange + DLX retry + competing consumers are a better fit for the handler dispatch pattern. Kafka's strengths (log replay, high throughput ordered streams) aren't needed for internal event routing.

**Kafka consumer idempotency**: DB-managed offsets (not Kafka consumer group offsets). Each message is recorded in a `KafkaMessage` table with `ON CONFLICT DO NOTHING` on `(topic, partition, offset)`.

---

## Decision 10: WebSocket Identity = Device Client ID (Not DB User)

**Chosen: `client_id` from URL path `/ws/{client_id}` is the primary identity**

```
  /ws/device-ABC-123          client_id = "device-ABC-123"
  /ws/sensor-unit-7           client_id = "sensor-unit-7"
```

- `client_id` is an opaque string -- could be a hardware serial, UUID, or any identifier
- One `client_id` can have multiple simultaneous connections (multiple tabs, reconnects)
- Auth is an **optional layer on top**: NoAuth returns `user=None` (anonymous), other strategies link to a DB `User`
- Event targeting uses `target_client_ids: list[str]` -- empty list means broadcast

|                                 | Device/Client ID Identity                            | DB User Identity                  |
|---------------------------------|------------------------------------------------------|-----------------------------------|
| **IoT / hardware devices**      | Natural fit                                          | Would need device-to-user mapping |
| **Anonymous connections**       | Supported (NoAuth)                                   | Not possible                      |
| **Multi-device per user**       | Each device has own ID                               | Would need user->devices lookup   |
| **Multi-connection per device** | ConnectionManager handles via `set[ConnectionState]` | Same                              |

---

## Decision 11: WebSocket Auth -- Strategy Pattern with Four Methods

**Chosen: Configurable via `WS_AUTH_METHOD` env var**

```
  WS_AUTH_METHOD=none          --> NoAuth (anonymous, user=None)
  WS_AUTH_METHOD=basic         --> HTTP Basic in upgrade headers
  WS_AUTH_METHOD=token         --> ?token=xxx query parameter
  WS_AUTH_METHOD=first_message --> {"type":"auth","token":"xxx"} as first frame
```

```
  Connection lifecycle with auth:

  Client                          Server
    |--- GET /ws/device-1 --------->|
    |<-- 101 Switching Protocols ---|   (always accept first)
    |                               |
    |   [auth phase depends on      |
    |    WS_AUTH_METHOD]            |
    |                               |
    |   none: skip                  |
    |   basic: check headers        |   (already available on upgrade)
    |   token: check query params   |   (already available on upgrade)
    |   first_message:              |
    |--- {"type":"auth","token":x} ->|  (must arrive within WS_AUTH_TIMEOUT)
    |                               |
    |<-- {"type":"connected"} ------|   (welcome message)
    |                               |
    |--- {"type":"ping"} ---------->|   (normal message flow)
    |<-- {"type":"pong"} -----------|
```

**Design choice**: Accept the WebSocket connection before auth. This allows sending an error message back before closing on auth failure, which is better UX than a raw connection refusal.

| Method          | Use Case                       | Tradeoff                              |
|-----------------|--------------------------------|---------------------------------------|
| `none`          | Development, internal networks | No security                           |
| `basic`         | Simple integrations            | Credentials in headers (TLS required) |
| `token`         | Browser clients, API keys      | Token visible in URL/logs             |
| `first_message` | Mobile/IoT with token rotation | Adds auth timeout latency             |

---

## Decision 12: WS Message Routing -- Decorator-Based `@ws_message_handler`

**Chosen: Mirror the `@event_handler` pattern**

```python
@ws_message_handler("ping")
async def handle_ping(payload: dict, ctx: MessageContext):
    await ctx.conn.ws.send_json({"type": "pong"})

@ws_message_handler("order.submit", model=OrderSubmitPayload)
async def handle_order(payload: OrderSubmitPayload, ctx: MessageContext):
    event = OrderSubmitted(client_id=ctx.conn.client_id, ...)
    async with ctx.session_factory() as session:
        await emit_event(event, session)
        await session.commit()
```

- Inbound messages are freeform JSON with a `type` field
- Handlers are exact-match on `type` (not patterns like event handlers)
- Optional Pydantic model for automatic validation + typed payload
- Errors (validation, handler exception) are caught and sent back as `{"type": "error", ...}`

**Alternative considered**: Using the event system for WS message dispatch (convert every inbound WS message to an event). Rejected because it adds unnecessary latency -- the message handler can emit events itself when needed, but simple request/response (like ping/pong) shouldn't go through the outbox.

---

## Decision 13: WS Outbound Push via Event Pipeline

**Chosen: Events with `target_client_ids` flow through the outbox like everything else**

```
  Any event source        Outbox        RabbitMQ       ws_push group      WS Client
  (REST, Kafka, WS)                                    (EventWorker)
        |                   |              |                |                  |
        |-- emit_event ---->|              |                |                  |
        |   (target_client_ |              |                |                  |
        |    ids=["dev-1"]) |              |                |                  |
        |                   |-- relay ---->|                |                  |
        |                   |              |-- deliver ---->|                  |
        |                   |              |  (ws.# pattern)|                  |
        |                   |              |                |-- send_json ---->|
        |                   |              |                |  (via manager)   |
```

**Why not push directly from the handler?** The outbox guarantees the push intent is persisted. If the WS server crashes, the event is still in RabbitMQ for redelivery. When running multiple WS server instances, RabbitMQ delivers to all instances' `ws_push` consumers, and each pushes to its locally connected clients.

**Broadcast**: `target_client_ids = []` (empty) triggers `manager.broadcast()`.

---

## Decision 14: Application-Level Heartbeat

**Chosen: `{"type": "ping"}` / `{"type": "pong"}` at the application layer**

```
  HeartbeatManager (background task)
  +------------------------------------------+
  |  every WS_HEARTBEAT_INTERVAL (30s):      |
  |    for each connection:                   |
  |      if now - last_seen > TIMEOUT (10s):  |
  |        close(code=4008)                   |
  |        manager.disconnect(conn)           |
  +------------------------------------------+
```

This is separate from the protocol-level WebSocket ping/pong (which uvicorn handles). Application-level heartbeat gives control over timeouts and allows tracking `last_seen` based on any client activity, not just pings.

|                              | Application Heartbeat              | Protocol Ping/Pong Only  |
|------------------------------|------------------------------------|--------------------------|
| **Client activity tracking** | `last_seen` updated on any message | Only on pong frames      |
| **Configurable timeout**     | `WS_HEARTBEAT_TIMEOUT` env var     | Uvicorn config only      |
| **Stale connection reaping** | HeartbeatManager background task   | Uvicorn's responsibility |
| **Observability**            | Application logs + metrics         | Opaque                   |

---

## Decision 15: Single-Process Dev, Splittable for Production

**Chosen: `EVENT_MODE` controls component composition**

```
  Development (EVENT_MODE=all):
  +-------------------------------------------+
  | Single Process                            |
  |  +-------+  +-------+  +--------------+   |
  |  | API   |  | Relay |  | Worker       |   |
  |  | routes|  |       |  | (all groups) |   |
  |  +-------+  +-------+  +--------------+   |
  |  +-------+  +------------+                |
  |  | Kafka |  | WS Server  |                |
  |  +-------+  +------------+                |
  +-------------------------------------------+

  Production (split by EVENT_MODE):
  +----------+  +------------+  +-------------------+  +------------------+
  | API      |  | Relay      |  | Worker            |  | WS Server        |
  | MODE=api |  | MODE=relay |  | MODE=worker       |  | MODE=api         |
  |          |  | relay      |  | GROUPS=analytics  |  | WS_ENABLED=true  |
  +----------+  +------------+  +-------------------+  +------------------+
                                +-------------------+
                                | Worker            |
                                | MODE=worker       |
                                | GROUPS=ws_push    |
                                +-------------------+
```

|                      | Single Mode Flag               | Separate Entrypoints     | Microservice Framework |
|----------------------|--------------------------------|--------------------------|------------------------|
| **Dev experience**   | One process, one command       | Multiple processes       | Heavy setup            |
| **Prod flexibility** | Same binary, different env var | Different binaries       | Native                 |
| **Code sharing**     | Natural (same codebase)        | Shared library           | Service boundaries     |
| **Testing**          | Easy (all in-process)          | Integration tests harder | Contract tests needed  |

---

## Decision 16: InMemoryBroker for Testing

**Chosen: Protocol-based broker swapping**

```python
# Production
broker = RabbitMQBroker()

# Tests
broker = InMemoryBroker()
broker.published  # list of (routing_key, message) tuples

# Direct handler dispatch (bypasses broker entirely)
await dispatch_to_handlers(event, session, groups={"hello"})
```

The `EventBroker` protocol (structural typing) allows tests to swap in `InMemoryBroker` without changing any application code. `dispatch_to_handlers` goes even further -- calling handlers directly for fast, deterministic integration tests.

---

## Summary: What We Built

```
  265 tests | 44 WS + 221 existing | all passing

  Source files:
    app/events/     -- base, registry, outbox, relay, broker, worker, handlers, celery, testing
    app/ws/         -- manager, auth, messages, heartbeat, endpoint, events, handlers
    app/kafka/      -- consumers, handlers, events, registry
    app/hello/      -- canonical example
    app/config.py   -- all configuration
    app/main.py     -- lifespan orchestration
    benchmarks/     -- 5 performance benchmarks

  Performance (RabbitMQ broker, after optimizations):
    Relay throughput:     6,680 events/sec
    Memory relay:        25,405 events/sec
    Concurrent emit+relay drain: 0.34s (was 6.97s before optimizations)
```
