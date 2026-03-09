# Resiliency Design

This document explains the structured concurrency and resiliency patterns
used in the application's background services.

## Problems Solved

### 1. Silent Task Death

**Before:** All background services (outbox relay, event worker, heartbeat
reaper, Kafka consumers) were launched with bare `asyncio.create_task()`.
If a task died from an unhandled exception, it stopped silently. The
exception was only surfaced if someone happened to `await` the task —
which only occurred during shutdown, where `except CancelledError: pass`
would swallow it anyway.

**Impact:** The outbox relay could crash and events would pile up in the
database indefinitely. Kafka consumers could die and messages would go
unprocessed. The WebSocket heartbeat reaper could stop and stale
connections would never be cleaned up. In all cases, the HTTP server
would continue serving requests as if nothing was wrong.

**After:** All background tasks run inside an `asyncio.TaskGroup` in the
application lifespan. If any task crashes, the `TaskGroup` automatically
cancels all sibling tasks and propagates the exception, causing the
process to exit. The orchestrator (systemd, Kubernetes, etc.) then
restarts the process, restoring all services to a clean state.

### 2. Cancellation During Critical Sections

**Before:** When the application shut down, `task.cancel()` was called on
all background tasks. This could interrupt operations at any point —
including mid-way through a relay batch where events had been published
to RabbitMQ but not yet marked as relayed in the database, or mid-way
through a Kafka message commit where the handler had run but the offset
hadn't been recorded.

**Impact:** On next startup, already-published events would be re-relayed
(duplicate delivery to RabbitMQ consumers) and already-processed Kafka
messages would be reprocessed (duplicate handling).

**After:** Critical commit operations are wrapped with `asyncio.shield()`:

- **OutboxRelay:** The `UPDATE ... SET relayed_at` + `COMMIT` after
  publishing events to the broker is shielded. If cancellation arrives
  during the publish phase, the shield ensures the database is updated
  consistently before the task exits.

- **KafkaConsumerService:** The `session.commit()` after processing a
  message and updating the offset is shielded. This prevents the
  scenario where a message is handled but its offset is never recorded.

### 3. No Health Visibility

**Before:** There was no way to know whether background services were
alive and functioning. A partial Kafka health endpoint existed but only
reported task existence, not liveness.

**After:** A `GET /health` endpoint reports the status of every background
service based on `last_active` timestamps. Each service updates its
timestamp on every successful loop iteration. The health endpoint
compares these against a threshold (default 60 seconds) and reports
`healthy`, `unhealthy`, or `starting`.

```json
{
  "status": "healthy",
  "services": [
    {"name": "outbox_relay", "status": "healthy", "last_active_ago": 2.3},
    {"name": "event_worker", "status": "healthy", "last_active_ago": 0.1},
    {"name": "kafka:feed_one", "status": "healthy", "last_active_ago": 5.7},
    {"name": "ws_heartbeat", "status": "healthy", "last_active_ago": 12.4}
  ]
}
```

This endpoint can be used as a Kubernetes liveness probe, a load
balancer health check, or a monitoring target.

### 4. Boilerplate-Heavy Lifecycle Management

**Before:** Every service had its own `_running` flag, `_task` attribute,
`start()` method that called `create_task()`, and `stop()` method with
the `task.cancel()` / `try: await task / except CancelledError: pass`
pattern. This was duplicated across four services and easy to get wrong.

**After:** Each service has a single `run()` coroutine that blocks until
cancelled. The lifespan launches all `run()` coroutines in a `TaskGroup`,
and cleanup happens automatically when the `TaskGroup` scope exits. The
old `start()`/`stop()` methods are preserved as thin convenience wrappers
for use in tests and CLI commands, but the core lifecycle is managed by
structured concurrency.

## Architecture

### Lifespan (main.py)

```
Application startup
    |
    v
Prepare services (connect broker, create instances)
    |
    v
asyncio.TaskGroup
    |-- relay.run()         # Outbox relay (PG LISTEN + poll loop)
    |-- worker.run()        # RabbitMQ consumer (one task per handler group)
    |-- heartbeat.run()     # WebSocket connection reaper
    |-- consumer1.run()     # Kafka consumer
    |-- consumer2.run()     # Kafka consumer
    |-- ...
    |
    |   [FastAPI serves HTTP requests here]
    |
    v
Shutdown triggered (SIGTERM / SIGINT)
    |
    v
CancelledError raised --> TaskGroup cancels all child tasks
    |
    v
Broker disconnected, cleanup complete
```

If any child task raises an exception other than `CancelledError`, the
`TaskGroup` cancels all other children and re-raises the exception. This
causes the ASGI server to shut down, and the process exits with a
non-zero code.

### Service `run()` Pattern

Each background service follows the same pattern:

```python
class SomeService:
    async def run(self) -> None:
        """Run until cancelled. Primary entry point."""
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._subtask_a())
                tg.create_task(self._subtask_b())
        except* Exception as eg:
            for exc in eg.exceptions:
                if not isinstance(exc, asyncio.CancelledError):
                    logger.error("Task failed", exc_info=exc)
            raise eg.exceptions[0] from None
        finally:
            logger.info("Service stopped")

    async def start(self) -> None:
        """Convenience: run as detached task."""
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Convenience: cancel a detached task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
```

Services that have only a single loop (heartbeat, Kafka consumer)
don't need an internal `TaskGroup` — their `run()` method just runs
the loop directly.

### CLI Standalone Mode

CLI commands (`app events relay`, `app events worker`) launch the
service's `run()` coroutine as a task alongside a shutdown signal
waiter using `asyncio.wait(return_when=FIRST_COMPLETED)`. This
preserves the ability to run services as separate processes for
production scalability:

```
app events relay   -->  relay.run() in background task
                        shutdown_event.wait() alongside
                        whichever finishes first wins
```

If the service crashes, the CLI reports the error. If a signal is
received, the service task is cancelled gracefully.

## `asyncio.shield` Usage

Shield is used narrowly and only where interruption would cause
data inconsistency:

| Location | What's Shielded | Why |
|---|---|---|
| `OutboxRelay._relay_batch` | `UPDATE + COMMIT` marking events relayed | Prevents publishing to broker but not marking DB, causing re-relay |
| `KafkaConsumerService._process_message` | `session.commit()` after handler + offset update | Prevents processing message but not recording offset, causing re-process |

Shield is **not** used for the publish phase itself — if a publish is
interrupted, the event remains un-relayed in the outbox and will be
picked up on the next relay cycle. This is by design: the outbox
pattern already guarantees at-least-once delivery.

## Health Endpoint

`GET /health` returns the aggregate status of all active background
services. Each service tracks a `last_active` monotonic timestamp
updated on every loop iteration:

- **OutboxRelay:** Updated after each relay cycle (poll or notify-triggered)
- **EventWorker:** Updated after each message is processed
- **KafkaConsumerService:** Updated after each message is processed
- **HeartbeatManager:** Updated after each reap cycle

A service is considered:
- `starting` — `last_active` is 0 (hasn't completed first iteration)
- `healthy` — `last_active` is within the threshold (default 60s)
- `unhealthy` — `last_active` is older than the threshold

The `GET /health/kafka` sub-endpoint provides detailed Kafka consumer
status including topics, group IDs, and per-consumer liveness.
