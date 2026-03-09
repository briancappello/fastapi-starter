# Benchmarks

Performance benchmarks for the event pipeline: outbox writes, relay throughput,
API latency under load, concurrent emit+relay contention, relay lifecycle with
structured concurrency, and full end-to-end pipeline with RabbitMQ consumer.

## Running

```bash
# Default benchmarks (outbox, relay, e2e, concurrent)
python -m benchmarks

# Specific benchmarks
python -m benchmarks --bench outbox relay
python -m benchmarks --bench concurrent

# Relay lifecycle (exercises run() with TaskGroup + LISTEN/NOTIFY)
python -m benchmarks --bench relay_lifecycle

# Full pipeline (requires RabbitMQ: emit → relay → RabbitMQ → worker → handler)
python -m benchmarks --bench pipeline --broker rabbitmq

# Quick smoke test
python -m benchmarks --events 500 --concurrency 5

# Full customization
python -m benchmarks --events 10000 --concurrency 20 --pool-size 15 --batch-size 200

# With real RabbitMQ (instead of in-memory broker)
python -m benchmarks --broker rabbitmq

# API latency (requires server running separately on port 8001)
python -m benchmarks --bench api --api-port 8001
```

## CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--bench` | `outbox relay e2e concurrent` | Which benchmarks to run |
| `--broker` | `memory` | `memory` or `rabbitmq` |
| `--events` | `5000` | Number of events to emit |
| `--concurrency` | `10` | Number of concurrent workers |
| `--pool-size` | `10` | DB connection pool size |
| `--batch-size` | `100` | Relay batch size |
| `--event-rate` | `500` | Background emit rate for API benchmark (events/sec) |
| `--api-port` | `8001` | Port where the API server is running |

## Benchmark Scenarios

### 1. Outbox Write (`outbox`)

Measures pure `emit_event` + `COMMIT` throughput.

```
  N concurrent workers
       |
       v
  emit_event(BenchmarkEvent) --> INSERT into event_outbox --> COMMIT
```

Tests DB write speed in isolation. Reveals connection pool pressure at high concurrency.

### 2. Relay Throughput (`relay`)

Seeds the outbox, then measures how fast the relay can read, publish, and mark events.

```
  Phase 1: Seed N events into outbox (batch INSERT)
  Phase 2: Relay all events
           SELECT ... FOR UPDATE SKIP LOCKED
           asyncio.gather(publish, publish, ...)
           bulk UPDATE relayed_at
```

With `--broker memory`: isolates DB read/write performance.
With `--broker rabbitmq`: includes real broker publish cost.

### 3. API Latency Under Load (`api`)

Measures HTTP response latency while the emit+relay pipeline runs in the background.

```
  +------------------+     +------------------+     +------------------+
  | HTTP clients     |     | Background       |     | Background       |
  | (N concurrent)   |     | emitters (5)     |     | relay (cont.)    |
  | GET / requests   |     | throttled to     |     |                  |
  |                  |     | --event-rate/s   |     |                  |
  +--------+---------+     +--------+---------+     +--------+---------+
           |                        |                        |
           +------------------------+------------------------+
                                    |
                              PostgreSQL
                         (shared connection pool)
```

Reveals DB-level contention (row locks, WAL, connection limits) across concurrent
workloads sharing the same PostgreSQL instance.

Requires a server running separately:
```bash
# Terminal 1
uvicorn app:app --port 8001

# Terminal 2
python -m benchmarks --bench api --api-port 8001
```

### 4. End-to-End Pipeline (`e2e`)

Two sequential phases measuring total pipeline throughput.

```
  Phase 1: Emit N events concurrently
  Phase 2: Relay all events to broker
  Report:  Total wall time, pipeline events/sec
```

### 5. Concurrent Emit + Relay (`concurrent`)

The most production-realistic scenario for the emit+relay stages. Emitters and
relay run simultaneously.

```
  +------------------+           +------------------+
  | Emitters (N)     |           | Relay            |
  | write to outbox  |           | SELECT FOR UPDATE|
  | concurrently     |           | SKIP LOCKED      |
  +--------+---------+           | (continuous)     |
           |                     +--------+---------+
           |                              |
           +------------------------------+
                        |
                  PostgreSQL
             (writer contention with relay)
```

After all emitters finish, the relay drains remaining events. Reports:
- Emit throughput and latency
- Relay throughput and latency
- Pending count at emit completion (how well relay keeps up)
- Drain time (how long to clear the backlog)
- Total wall time

### 6. Relay Lifecycle (`relay_lifecycle`)

Exercises the full `OutboxRelay.run()` lifecycle instead of calling `_relay_batch()`
directly. Tests the structured concurrency path built with `asyncio.TaskGroup`:

```
  Phase 1: Seed N events into outbox (batch INSERT)
  Phase 2: Start relay.run() as background task
           ┌─ TaskGroup ─────────────────────────────┐
           │  Task 1: _listen_notifications()         │
           │          PG LISTEN on event_outbox_channel│
           │  Task 2: _relay_loop()                   │
           │          wait for NOTIFY or poll_interval │
           │          _relay_batch() → publish → mark  │
           └──────────────────────────────────────────┘
  Phase 3: Monitor progress, cancel when done
```

Compared to Benchmark 2 (which calls `_relay_batch()` in a tight loop), this
measures real-world throughput including LISTEN/NOTIFY wakeup latency, the
`asyncio.Event` wait/clear cycle, and TaskGroup overhead.

### 7. Full Pipeline (`pipeline`)

**Requires `--broker rabbitmq`.** The only benchmark exercising the complete
production path from outbox write through to handler completion.

```
  +------------------+     +------------------+     +------------------+
  | Emitters (N)     |     | OutboxRelay.run()|     | EventWorker.run()|
  | emit_event()     |---->| TaskGroup +      |---->| Consume from     |
  | INSERT outbox    |     | LISTEN/NOTIFY    |     | RabbitMQ queue   |
  +------------------+     | Publish to AMQP  |     | Dispatch handler |
                           +------------------+     | INSERT processed |
                                                    | session.commit() |
                                                    +------------------+
```

The benchmark handler does real DB work: INSERT into `processed_event` table.

Metrics:
- Emit throughput (events/sec)
- Handler throughput (events/sec)
- End-to-end latency: `event.timestamp` → handler execution (P50/P95/P99)
- Total wall time
- Handlers completed vs events emitted (verification)

### 8. Full Load (`full_load`)

**Requires `--broker rabbitmq` and a running API server.** The most complete
production simulation. All five workloads compete for the same PostgreSQL instance
simultaneously:

```
  +------------------+  +------------------+  +------------------+  +------------------+
  | HTTP clients (N) |  | Emitters (5)     |  | OutboxRelay.run()|  | EventWorker.run()|
  | GET / requests   |  | throttled to     |  | TaskGroup +      |  | Consume from     |
  |                  |  | --event-rate/s   |  | LISTEN/NOTIFY    |  | RabbitMQ queue   |
  +--------+---------+  +--------+---------+  | Publish to AMQP  |  | Dispatch handler |
           |                     |             +--------+---------+  | INSERT processed |
           |                     |                      |            +--------+---------+
           +---------------------+----------------------+---------------------+
                                              |
                                        PostgreSQL
                                   (5-way contention)
```

Combines the API latency measurement of Benchmark 3 with the full pipeline
of Benchmark 7. Background emitters run at a throttled rate (`--event-rate`)
while HTTP clients hammer the API. The relay and worker run their real `run()`
lifecycles with structured concurrency.

After the HTTP phase completes, the pipeline drains any remaining events.

Requires a server running separately:
```bash
# Terminal 1
uvicorn app.main:app --port 8001

# Terminal 2
python -m benchmarks --bench full_load --event-rate 200 --api-port 8001
```

Metrics:
- API latency (P50/P95/P99) under full pipeline load
- Background emit throughput
- Handler throughput and E2E latency
- Total events emitted and processed during the HTTP window

## Statistics

Each benchmark reports via `StatsCollector`:

```
  === Outbox Write ===
  Total ops:    5000 (0 errors)
  Wall time:    1.23s
  Throughput:   4065.04 ops/sec
  Latency:
    min:    0.12ms
    p50:    0.23ms
    p95:    0.45ms
    p99:    0.89ms
    max:    2.34ms
    mean:   0.25ms
  Pool: size=10 checked_out=0 overflow=0 checked_in=10
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package init |
| `__main__.py` | Entry point (`python -m benchmarks`) |
| `run.py` | All 5 benchmarks, CLI arg parsing, `BenchmarkEvent` |
| `stats.py` | `StatsCollector` (latency percentiles, throughput), `PoolStats` |

## Notes

- Benchmarks use a standalone SQLAlchemy engine with tunable pool parameters,
  separate from the application's engine.
- All benchmark events use `event_type = "benchmark.event"` and are cleaned up
  after each run.
- The `memory` broker (`InMemoryBroker`) isolates DB performance from broker
  overhead. Use `rabbitmq` to measure the full pipeline including network I/O.
- The `pipeline` benchmark requires a running RabbitMQ instance because
  `EventWorker` is coupled to `RabbitMQBroker` (it needs `broker.channel`).
- The `relay_lifecycle` benchmark uses the full `OutboxRelay.run()` with
  PG LISTEN/NOTIFY, requiring the `notify_event_outbox` trigger to exist
  (created by the `0ec60bc5c0c2` migration).
- The `pipeline` benchmark registers a handler dynamically and cleans up
  both the `event_outbox` and `processed_event` tables after each run.
  It also purges the `handlers.benchmark` RabbitMQ queue to prevent
  cross-run contamination.
