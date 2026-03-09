"""Benchmark runner for the event-driven architecture.

Measures throughput and latency at each stage of the pipeline:
1. Outbox write throughput (emit_event)
2. Relay throughput (outbox → broker) — calls _relay_batch() directly
3. API latency under event load
4. End-to-end pipeline (emit → relay, sequential phases)
5. Concurrent emit + relay (simultaneous writers and relay)
6. Relay lifecycle — uses OutboxRelay.run() with TaskGroup + LISTEN/NOTIFY
7. Full pipeline — emit → relay → RabbitMQ → EventWorker → handler (requires RabbitMQ)

Usage:
    # Run default benchmarks (1-5)
    python -m benchmarks

    # Run relay lifecycle benchmark
    python -m benchmarks --bench relay_lifecycle

    # Run full pipeline (requires RabbitMQ)
    python -m benchmarks --bench pipeline --broker rabbitmq

    # Customize parameters
    python -m benchmarks --events 10000 --concurrency 20

    # Quick smoke test
    python -m benchmarks --events 500 --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from typing import TYPE_CHECKING, Literal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


if TYPE_CHECKING:
    from app.events.broker import EventBroker

# ---------------------------------------------------------------------------
# Benchmark event type (defined here to avoid polluting the app registry
# in production — these are only used during benchmarks)
# ---------------------------------------------------------------------------
from app.events.base import Event

from .stats import PoolStats, StatsCollector, print_report


class BenchmarkEvent(Event):
    """Synthetic event for benchmarks."""

    event_type: Literal["benchmark.event"] = "benchmark.event"  # type: ignore[assignment]
    seq: int


# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------

BrokerKind = Literal["memory", "rabbitmq"]


async def create_broker(kind: BrokerKind) -> "EventBroker":
    """Create and start the appropriate broker for benchmarks."""
    from app.events.broker import EventBroker  # noqa: F811 (type import)

    if kind == "rabbitmq":
        from app.events.broker import RabbitMQBroker

        broker = RabbitMQBroker()
        await broker.start()
        return broker
    else:
        from app.events.testing import InMemoryBroker

        broker = InMemoryBroker()
        await broker.start()
        return broker


async def create_bench_engine(pool_size: int = 10, max_overflow: int = 20):
    """Create a separate engine for benchmarks with tunable pool."""
    from app.config import Config

    engine = create_async_engine(
        Config.SQL_DB_URL,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=30,
        pool_pre_ping=True,
        echo=False,
    )
    return engine


async def cleanup_outbox(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Delete all benchmark events from the outbox. Returns rows deleted."""
    from app.db.models.event_outbox import EventOutbox

    async with session_factory() as session:
        result = await session.execute(
            delete(EventOutbox).where(EventOutbox.event_type == "benchmark.event")
        )
        await session.commit()
        return result.rowcount or 0  # type: ignore[return-value]


async def count_pending(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Count un-relayed outbox rows for benchmark events."""
    from app.db.models.event_outbox import EventOutbox

    async with session_factory() as session:
        result = await session.execute(
            select(func.count(EventOutbox.id)).where(
                EventOutbox.event_type == "benchmark.event",
                EventOutbox.relayed_at.is_(None),
            )
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Benchmark 1: Outbox write throughput
# ---------------------------------------------------------------------------


async def bench_outbox_write(
    num_events: int,
    concurrency: int,
    pool_size: int,
) -> StatsCollector:
    """Measure how fast we can emit events to the outbox.

    Each operation: create Event + INSERT to event_outbox + COMMIT.
    Runs `concurrency` parallel workers each emitting events.
    """
    from app.events.outbox import emit_event

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Cleanup any leftover benchmark events
    await cleanup_outbox(session_factory)

    stats = StatsCollector("Outbox Write (emit_event)")
    counter = 0
    lock = asyncio.Lock()

    async def worker():
        nonlocal counter
        while True:
            async with lock:
                seq = counter
                counter += 1
                if seq >= num_events:
                    return

            event = BenchmarkEvent(source="bench:outbox", seq=seq)
            async with session_factory() as session:
                with stats.measure():
                    await emit_event(event, session)
                    await session.commit()

    stats.start_clock()
    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.gather(*tasks)

    # Pool stats at end
    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    # Cleanup benchmark events
    await cleanup_outbox(session_factory)
    await engine.dispose()

    print(stats.report())
    print(f"  {pool_snapshot}")
    return stats


# ---------------------------------------------------------------------------
# Benchmark 2: Relay throughput
# ---------------------------------------------------------------------------


async def bench_relay(
    num_events: int,
    batch_size: int,
    pool_size: int,
    broker_kind: BrokerKind = "memory",
) -> StatsCollector:
    """Measure relay throughput: read from outbox, publish to broker, mark relayed.

    With ``broker_kind="memory"`` this isolates DB read/write performance.
    With ``broker_kind="rabbitmq"`` it includes the real broker publish cost.
    """
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Cleanup and seed events
    await cleanup_outbox(session_factory)
    print(f"  Seeding {num_events:,} events into outbox...")
    for i in range(0, num_events, 500):
        batch_end = min(i + 500, num_events)
        async with session_factory() as session:
            for seq in range(i, batch_end):
                event = BenchmarkEvent(source="bench:relay", seq=seq)
                await emit_event(event, session)
            await session.commit()

    pending = await count_pending(session_factory)
    print(f"  Outbox has {pending:,} pending events")
    print(f"  Broker: {broker_kind}")

    broker = await create_broker(broker_kind)
    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
    )

    stats = StatsCollector("Relay (outbox → broker)")
    stats.start_clock()

    # Relay all events, measuring each batch
    total_relayed = 0
    while True:
        with stats.measure():
            count = await relay._relay_batch()
        if count == 0:
            break
        total_relayed += count

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    await broker.stop()
    await cleanup_outbox(session_factory)
    await engine.dispose()

    print(stats.report())
    print(f"  Batch size:    {batch_size}")
    print(f"  Total relayed: {total_relayed:,}")
    print(
        f"  Events/sec:    {total_relayed / stats.elapsed:,.0f} (vs batch ops/sec above)"
    )
    print(f"  {pool_snapshot}")
    return stats


# ---------------------------------------------------------------------------
# Benchmark 3: API latency under event load
# ---------------------------------------------------------------------------


async def bench_api_latency(
    num_requests: int,
    concurrency: int,
    event_load_rate: int,
    pool_size: int = 10,
    batch_size: int = 100,
    broker_kind: BrokerKind = "memory",
    api_port: int = 8001,
) -> tuple[StatsCollector, StatsCollector, StatsCollector]:
    """Measure API p99 latency while emit+relay pipeline is running.

    Starts background workers that continuously emit events to simulate
    Kafka consumer load, plus a relay draining the outbox to the broker,
    while concurrent HTTP clients hit the API. This reveals DB-level
    contention (row locks, WAL, connection limits) across all three
    workloads sharing the same PostgreSQL instance.

    The benchmark process uses its own DB pool (separate from the server's),
    but they share the same PostgreSQL backend, so DB-level contention
    (locks, I/O, WAL writes) is realistic.
    """
    import httpx

    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay

    engine = await create_bench_engine(pool_size=pool_size, max_overflow=pool_size * 2)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    await cleanup_outbox(session_factory)

    api_stats = StatsCollector("API Latency (GET /)")
    emit_stats = StatsCollector("Background Emit")
    relay_stats = StatsCollector("Background Relay")
    stop_bg = asyncio.Event()

    total_relayed = 0
    relay_lock = asyncio.Lock()

    # Background event emitter (simulates Kafka consumer load)
    async def event_emitter():
        seq = 0
        while not stop_bg.is_set():
            event = BenchmarkEvent(source="bench:api-load", seq=seq)
            try:
                async with session_factory() as session:
                    with emit_stats.measure():
                        await emit_event(event, session)
                        await session.commit()
                seq += 1
            except Exception:
                emit_stats.record_error()

            # Throttle to target rate per emitter
            if event_load_rate > 0:
                await asyncio.sleep(1.0 / event_load_rate)

    # Background relay loop
    broker = await create_broker(broker_kind)
    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
    )

    async def relay_loop():
        nonlocal total_relayed
        while not stop_bg.is_set():
            with relay_stats.measure():
                count = await relay._relay_batch()
            if count > 0:
                async with relay_lock:
                    total_relayed += count
            else:
                try:
                    await asyncio.wait_for(stop_bg.wait(), timeout=0.01)
                except asyncio.TimeoutError:
                    pass

        # Drain remaining
        while True:
            with relay_stats.measure():
                count = await relay._relay_batch()
            if count == 0:
                break
            async with relay_lock:
                total_relayed += count

    # HTTP client workers
    async def http_worker(client: httpx.AsyncClient, count: int):
        for _ in range(count):
            with api_stats.measure():
                try:
                    resp = await client.get("/")
                    if resp.status_code != 200:
                        api_stats.record_error()
                except Exception:
                    api_stats.record_error()

    # Start background emit + relay
    num_emitters = min(concurrency, 5)
    print(f"  Broker: {broker_kind}")
    print(f"  Background: {num_emitters} emitters + 1 relay")
    print(f"  HTTP concurrency: {concurrency}")

    emit_stats.start_clock()
    relay_stats.start_clock()
    bg_emitters = [asyncio.create_task(event_emitter()) for _ in range(num_emitters)]
    bg_relay = asyncio.create_task(relay_loop())

    # Wait a moment for background load to ramp up
    await asyncio.sleep(0.5)

    # Run HTTP benchmark
    base_url = f"http://127.0.0.1:{api_port}"
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=10.0,
    ) as client:
        # Quick connectivity check
        try:
            resp = await client.get("/")
            if resp.status_code != 200:
                print(
                    f"  WARNING: API returned {resp.status_code}. Is the server running?"
                )
                stop_bg.set()
                for t in bg_emitters:
                    t.cancel()
                bg_relay.cancel()
                await broker.stop()
                await engine.dispose()
                return api_stats, emit_stats, relay_stats
        except httpx.ConnectError:
            print(
                f"  ERROR: Cannot connect to {base_url}\n"
                f"  Start the server first: uv run uvicorn app.main:app --port {api_port}"
            )
            stop_bg.set()
            for t in bg_emitters:
                t.cancel()
            bg_relay.cancel()
            await broker.stop()
            await engine.dispose()
            return api_stats, emit_stats, relay_stats

        api_stats.start_clock()
        per_worker = num_requests // concurrency
        remainder = num_requests % concurrency
        workers = []
        for i in range(concurrency):
            count = per_worker + (1 if i < remainder else 0)
            workers.append(asyncio.create_task(http_worker(client, count)))
        await asyncio.gather(*workers)

    # Stop background load
    stop_bg.set()
    for t in bg_emitters:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    await bg_relay

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    await broker.stop()
    await cleanup_outbox(session_factory)
    await engine.dispose()

    print()
    print(api_stats.report())
    print()
    print(emit_stats.report())
    print()
    print(relay_stats.report())
    relay_event_rate = (
        total_relayed / relay_stats.elapsed if relay_stats.elapsed > 0 else 0
    )
    print(f"  Relay events/sec: {relay_event_rate:,.0f}")
    print(f"  Total relayed:    {total_relayed:,}")
    print(f"  {pool_snapshot}")
    return api_stats, emit_stats, relay_stats


# ---------------------------------------------------------------------------
# Benchmark 4: End-to-end pipeline
# ---------------------------------------------------------------------------


async def bench_end_to_end(
    num_events: int,
    concurrency: int,
    pool_size: int,
    broker_kind: BrokerKind = "memory",
) -> tuple[StatsCollector, StatsCollector]:
    """Measure end-to-end: emit → relay → handler dispatch.

    Emits events, then runs the relay.
    Measures both emit throughput and relay throughput separately,
    plus total wall time for the full pipeline.
    """
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    await cleanup_outbox(session_factory)

    emit_stats = StatsCollector("E2E Phase 1: Emit")
    relay_stats = StatsCollector("E2E Phase 2: Relay")

    # Phase 1: Emit events
    counter = 0
    lock = asyncio.Lock()

    async def emitter():
        nonlocal counter
        while True:
            async with lock:
                seq = counter
                counter += 1
                if seq >= num_events:
                    return
            event = BenchmarkEvent(source="bench:e2e", seq=seq)
            async with session_factory() as session:
                with emit_stats.measure():
                    await emit_event(event, session)
                    await session.commit()

    print(f"  Phase 1: Emitting {num_events:,} events...")
    emit_stats.start_clock()
    tasks = [asyncio.create_task(emitter()) for _ in range(concurrency)]
    await asyncio.gather(*tasks)

    pending = await count_pending(session_factory)
    print(f"    Done. {pending:,} pending in outbox.")

    # Phase 2: Relay all
    print(f"  Phase 2: Relaying (broker={broker_kind})...")
    broker = await create_broker(broker_kind)
    relay = OutboxRelay(broker=broker, session_factory=session_factory, batch_size=200)

    relay_stats.start_clock()
    total_relayed = 0
    while True:
        with relay_stats.measure():
            count = await relay._relay_batch()
        if count == 0:
            break
        total_relayed += count

    await broker.stop()

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    print(f"    Done. {total_relayed:,} relayed.")

    total_time = emit_stats.elapsed + relay_stats.elapsed

    await cleanup_outbox(session_factory)
    await engine.dispose()

    print()
    print(emit_stats.report())
    print()
    print(relay_stats.report())
    print(f"  Relay events/sec: {total_relayed / relay_stats.elapsed:,.0f}")
    print()
    print(f"=== End-to-End Summary ===")
    print(f"  Total events:  {num_events:,}")
    print(f"  Broker:        {broker_kind}")
    print(f"  Emit time:     {emit_stats.elapsed:.2f}s")
    print(f"  Relay time:    {relay_stats.elapsed:.2f}s")
    print(f"  Total time:    {total_time:.2f}s")
    print(f"  Pipeline rate: {num_events / total_time:,.0f} events/sec")
    print(f"  {pool_snapshot}")

    return emit_stats, relay_stats


# ---------------------------------------------------------------------------
# Benchmark 5: Concurrent emit + relay
# ---------------------------------------------------------------------------


async def bench_concurrent(
    num_events: int,
    concurrency: int,
    pool_size: int,
    batch_size: int,
    broker_kind: BrokerKind = "memory",
) -> tuple[StatsCollector, StatsCollector]:
    """Measure throughput when emit and relay run simultaneously.

    This is the realistic production scenario: Kafka consumers (or API
    handlers) write events to the outbox while the relay continuously
    drains it. Reveals contention between writers and the relay's
    SELECT FOR UPDATE SKIP LOCKED.

    The relay runs continuously in the background. Emitters write
    ``num_events`` events concurrently. We measure:
    - Emit throughput/latency (under relay contention)
    - Relay throughput/latency (under write contention)
    - How quickly the outbox drains after all emits finish
    """
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    await cleanup_outbox(session_factory)

    emit_stats = StatsCollector("Concurrent Emit")
    relay_stats = StatsCollector("Concurrent Relay")
    stop_relay = asyncio.Event()

    broker = await create_broker(broker_kind)
    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
    )

    total_relayed = 0
    relay_lock = asyncio.Lock()

    # Relay loop: runs continuously until stopped
    async def relay_loop():
        nonlocal total_relayed
        while not stop_relay.is_set():
            with relay_stats.measure():
                count = await relay._relay_batch()
            if count > 0:
                async with relay_lock:
                    total_relayed += count
            else:
                # No pending events, brief pause to avoid busy-spinning
                try:
                    await asyncio.wait_for(stop_relay.wait(), timeout=0.01)
                except asyncio.TimeoutError:
                    pass

        # Drain remaining events after emitters finish
        while True:
            with relay_stats.measure():
                count = await relay._relay_batch()
            if count == 0:
                break
            async with relay_lock:
                total_relayed += count

    # Emitter workers
    counter = 0
    counter_lock = asyncio.Lock()

    async def emitter():
        nonlocal counter
        while True:
            async with counter_lock:
                seq = counter
                counter += 1
                if seq >= num_events:
                    return
            event = BenchmarkEvent(source="bench:concurrent", seq=seq)
            async with session_factory() as session:
                with emit_stats.measure():
                    await emit_event(event, session)
                    await session.commit()

    print(f"  Broker: {broker_kind}")
    print(f"  Starting relay + {concurrency} emitters concurrently...")

    # Start both emit and relay at the same time
    emit_stats.start_clock()
    relay_stats.start_clock()

    relay_task = asyncio.create_task(relay_loop())
    emit_tasks = [asyncio.create_task(emitter()) for _ in range(concurrency)]

    # Wait for all emitters to finish
    await asyncio.gather(*emit_tasks)
    emit_done_time = time.monotonic()
    emit_elapsed = emit_done_time - (emit_stats._start_time or emit_done_time)

    # Check how many are still pending
    pending_at_emit_done = await count_pending(session_factory)
    print(f"  All {num_events:,} emitted in {emit_elapsed:.2f}s")
    print(f"  Pending at emit completion: {pending_at_emit_done:,}")

    # Signal relay to drain and stop
    stop_relay.set()
    await relay_task

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)
    drain_time = (
        relay_stats.elapsed - emit_elapsed if relay_stats.elapsed > emit_elapsed else 0
    )

    remaining = await count_pending(session_factory)

    await broker.stop()
    await cleanup_outbox(session_factory)
    await engine.dispose()

    print(f"  Relay drained remaining in {drain_time:.2f}s")
    print(f"  Events remaining in outbox: {remaining:,}")
    print()
    print(emit_stats.report())
    print()
    print(relay_stats.report())
    relay_event_rate = (
        total_relayed / relay_stats.elapsed if relay_stats.elapsed > 0 else 0
    )
    print(f"  Relay events/sec: {relay_event_rate:,.0f}")
    print(f"  Total relayed:    {total_relayed:,}")
    print()
    print(f"=== Concurrent Summary ===")
    print(f"  Total events:         {num_events:,}")
    print(f"  Broker:               {broker_kind}")
    print(f"  Emit throughput:      {emit_stats.throughput:,.0f} events/sec")
    print(f"  Relay throughput:     {relay_event_rate:,.0f} events/sec")
    print(f"  Pending at emit end:  {pending_at_emit_done:,}")
    print(f"  Drain time:           {drain_time:.2f}s")
    print(f"  Total wall time:      {relay_stats.elapsed:.2f}s")
    print(f"  {pool_snapshot}")

    return emit_stats, relay_stats


# ---------------------------------------------------------------------------
# Benchmark 6: Relay lifecycle (uses OutboxRelay.run())
# ---------------------------------------------------------------------------


async def bench_relay_lifecycle(
    num_events: int,
    batch_size: int,
    pool_size: int,
    broker_kind: BrokerKind = "memory",
) -> StatsCollector:
    """Measure relay throughput using the real ``OutboxRelay.run()`` lifecycle.

    Unlike :func:`bench_relay` which calls ``_relay_batch()`` directly in a
    tight loop, this benchmark exercises the full relay lifecycle:

    - ``asyncio.TaskGroup`` with two concurrent tasks
    - PG LISTEN/NOTIFY for instant wakeup on new outbox rows
    - Polling fallback every ``poll_interval`` seconds
    - Structured error handling

    Seeds events into the outbox, starts ``relay.run()`` as a background
    task, and monitors progress until all events are relayed.
    """
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Cleanup and seed events
    await cleanup_outbox(session_factory)
    print(f"  Seeding {num_events:,} events into outbox...")
    for i in range(0, num_events, 500):
        batch_end = min(i + 500, num_events)
        async with session_factory() as session:
            for seq in range(i, batch_end):
                event = BenchmarkEvent(source="bench:relay-lifecycle", seq=seq)
                await emit_event(event, session)
            await session.commit()

    pending = await count_pending(session_factory)
    print(f"  Outbox has {pending:,} pending events")
    print(f"  Broker: {broker_kind}")

    broker = await create_broker(broker_kind)
    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
        poll_interval=1.0,  # Shorter for benchmarks (NOTIFY should wake it faster)
    )

    stats = StatsCollector("Relay Lifecycle (run())")
    stats.start_clock()

    # Start relay.run() as a background task — this is the real lifecycle
    relay_task = asyncio.create_task(relay.run(), name="bench-relay-lifecycle")

    # Monitor progress: poll until all events are relayed
    check_interval = 0.1  # seconds between progress checks
    last_pending = pending
    stall_count = 0
    max_stall = 100  # timeout after 10s of no progress

    while True:
        await asyncio.sleep(check_interval)
        current_pending = await count_pending(session_factory)

        if current_pending == 0:
            break

        if current_pending == last_pending:
            stall_count += 1
            if stall_count >= max_stall:
                print(f"  WARNING: Relay stalled with {current_pending:,} events pending")
                break
        else:
            stall_count = 0
            last_pending = current_pending

    # Cancel the relay (it runs forever by design)
    relay_task.cancel()
    try:
        await relay_task
    except (asyncio.CancelledError, Exception):
        pass

    remaining = await count_pending(session_factory)
    total_relayed = pending - remaining

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    await broker.stop()
    await cleanup_outbox(session_factory)
    await engine.dispose()

    elapsed = stats.elapsed
    events_per_sec = total_relayed / elapsed if elapsed > 0 else 0

    print(f"=== Relay Lifecycle (run()) ===")
    print(f"  Wall time:     {elapsed:.2f}s")
    print(f"  Batch size:    {batch_size}")
    print(f"  Total relayed: {total_relayed:,}")
    print(f"  Remaining:     {remaining:,}")
    print(f"  Events/sec:    {events_per_sec:,.0f}")
    print(f"  {pool_snapshot}")
    return stats


# ---------------------------------------------------------------------------
# Benchmark 7: Full pipeline (emit → relay → RabbitMQ → worker → handler)
# ---------------------------------------------------------------------------

# Module-level state shared between the benchmark driver and the handler
# function. The handler runs inside EventWorker's asyncio loop (same event
# loop) so plain asyncio primitives are safe.
_pipeline_counter: int = 0
_pipeline_target: int = 0
_pipeline_done: asyncio.Event | None = None
_pipeline_lock: asyncio.Lock | None = None
_pipeline_handler_stats: StatsCollector | None = None
_pipeline_session_factory: async_sessionmaker | None = None  # type: ignore[type-arg]


async def _benchmark_handler(event: "Event", session: AsyncSession) -> None:
    """Benchmark handler: records a processed_event row and tracks completion.

    This handler does real DB work (INSERT into processed_event) to simulate
    production handler behavior with real DB contention and commit overhead.
    The commit is done by the EventWorker after all handlers for the group
    run, so we just need to add the row to the session.
    """
    global _pipeline_counter

    from app.db.models.processed_event import ProcessedEvent

    # Real DB work: record that we processed this event
    session.add(
        ProcessedEvent(
            event_id=event.event_id,
            handler_group="benchmark",
        )
    )

    # Track handler latency (time from event creation to handler execution)
    if _pipeline_handler_stats is not None:
        event_created = event.timestamp.timestamp()
        now = time.time()
        _pipeline_handler_stats.record(now - event_created)

    # Increment completion counter
    if _pipeline_lock is not None and _pipeline_done is not None:
        async with _pipeline_lock:
            _pipeline_counter += 1
            if _pipeline_counter >= _pipeline_target:
                _pipeline_done.set()


async def cleanup_processed_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Delete all benchmark processed_event rows. Returns rows deleted."""
    from app.db.models.processed_event import ProcessedEvent

    async with session_factory() as session:
        result = await session.execute(
            delete(ProcessedEvent).where(ProcessedEvent.handler_group == "benchmark")
        )
        await session.commit()
        return result.rowcount or 0  # type: ignore[return-value]


async def bench_pipeline(
    num_events: int,
    concurrency: int,
    pool_size: int,
    batch_size: int,
    broker_kind: BrokerKind = "rabbitmq",
) -> tuple[StatsCollector, StatsCollector, StatsCollector]:
    """Measure the full event pipeline: emit → relay → RabbitMQ → worker → handler.

    This is the only benchmark that exercises the complete production path,
    including:

    - Outbox writes (``emit_event``)
    - ``OutboxRelay.run()`` with TaskGroup + LISTEN/NOTIFY
    - RabbitMQ publish via the broker
    - ``EventWorker.run()`` consuming from RabbitMQ
    - Handler dispatch with real DB work (INSERT into processed_event)

    **Requires ``--broker rabbitmq``** since ``EventWorker`` is coupled to
    ``RabbitMQBroker`` (needs ``broker.channel``).

    Metrics reported:
    - Emit throughput (events/sec)
    - Handler throughput (events/sec)
    - End-to-end latency: event.timestamp → handler execution (P50/P95/P99)
    - Total wall time
    """
    global _pipeline_counter, _pipeline_target, _pipeline_done
    global _pipeline_lock, _pipeline_handler_stats, _pipeline_session_factory

    if broker_kind != "rabbitmq":
        print("  ERROR: Pipeline benchmark requires --broker rabbitmq")
        print("  (EventWorker is coupled to RabbitMQBroker)")
        empty = StatsCollector("Skipped")
        return empty, empty, empty

    from app.events.broker import RabbitMQBroker
    from app.events.handlers import handler_registry
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay
    from app.events.worker import EventWorker

    engine = await create_bench_engine(pool_size=pool_size)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Cleanup from any previous run
    await cleanup_outbox(session_factory)
    await cleanup_processed_events(session_factory)

    # Register benchmark handler dynamically (avoid polluting registry
    # when running other benchmarks). We'll remove it at the end.
    handler_registry.register(
        event_pattern="benchmark.event",
        group="benchmark",
        handler=_benchmark_handler,
    )

    # Initialize pipeline synchronization state
    _pipeline_counter = 0
    _pipeline_target = num_events
    _pipeline_done = asyncio.Event()
    _pipeline_lock = asyncio.Lock()
    _pipeline_handler_stats = StatsCollector("Pipeline E2E Latency")
    _pipeline_session_factory = session_factory

    # Create broker and services
    broker = await create_broker(broker_kind)
    assert isinstance(broker, RabbitMQBroker), "Pipeline requires RabbitMQBroker"

    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
        poll_interval=1.0,
    )

    worker = EventWorker(
        broker=broker,
        session_factory=session_factory,
        groups={"benchmark"},
    )

    print(f"  Broker: {broker_kind}")
    print(f"  Target: {num_events:,} events through full pipeline")
    print(f"  Handler: INSERT into processed_event (real DB work)")

    # Purge any leftover messages from previous runs
    channel = broker.channel
    try:
        queue = await channel.declare_queue(
            "handlers.benchmark", durable=True, passive=True
        )
        purge_result = await queue.purge()
        purge_count = getattr(purge_result, "message_count", purge_result)
        if purge_count:
            print(f"  Purged {purge_count} leftover messages from handlers.benchmark")
    except Exception:
        pass  # Queue doesn't exist yet, that's fine

    # Start relay and worker
    relay_task = asyncio.create_task(relay.run(), name="bench-pipeline-relay")
    worker_task = asyncio.create_task(worker.run(), name="bench-pipeline-worker")

    # Give worker time to set up queues and start consuming
    await asyncio.sleep(1.0)

    # Phase 1: Emit events
    emit_stats = StatsCollector("Pipeline Emit")
    counter = 0
    emit_lock = asyncio.Lock()

    async def emitter():
        nonlocal counter
        while True:
            async with emit_lock:
                seq = counter
                counter += 1
                if seq >= num_events:
                    return
            event = BenchmarkEvent(source="bench:pipeline", seq=seq)
            async with session_factory() as session:
                with emit_stats.measure():
                    await emit_event(event, session)
                    await session.commit()

    print(f"  Emitting {num_events:,} events with {concurrency} workers...")
    emit_stats.start_clock()
    _pipeline_handler_stats.start_clock()
    emit_tasks = [asyncio.create_task(emitter()) for _ in range(concurrency)]
    await asyncio.gather(*emit_tasks)
    print(f"  All events emitted in {emit_stats.elapsed:.2f}s")

    # Phase 2: Wait for all events to be processed by handlers
    timeout = max(60, num_events * 0.1)  # generous timeout
    print(f"  Waiting for handler to process all events (timeout={timeout:.0f}s)...")
    try:
        await asyncio.wait_for(_pipeline_done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        print(
            f"  WARNING: Timed out! Processed {_pipeline_counter:,}/{num_events:,} events"
        )

    # Brief pause to let the worker's final session.commit() complete
    # (the handler fires _pipeline_done before the worker commits)
    await asyncio.sleep(0.5)

    handler_stats = _pipeline_handler_stats

    # Stop services
    relay_task.cancel()
    worker_task.cancel()
    for task in (relay_task, worker_task):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    # Verify: count processed_event rows
    from app.db.models.processed_event import ProcessedEvent

    async with session_factory() as session:
        result = await session.execute(
            select(func.count(ProcessedEvent.id)).where(
                ProcessedEvent.handler_group == "benchmark"
            )
        )
        processed_count = result.scalar_one()

    remaining = await count_pending(session_factory)
    total_wall = handler_stats.elapsed if handler_stats else 0

    # Cleanup
    await cleanup_outbox(session_factory)
    await cleanup_processed_events(session_factory)

    # Remove the benchmark handler from registry to avoid pollution
    handler_registry._handlers = [
        h for h in handler_registry._handlers if h.group != "benchmark"
    ]

    await broker.stop()
    await engine.dispose()

    # Reset module-level state
    _pipeline_counter = 0
    _pipeline_target = 0
    _pipeline_done = None
    _pipeline_lock = None
    _pipeline_handler_stats = None
    _pipeline_session_factory = None

    # Report results
    print()
    print(emit_stats.report())
    print()
    print(handler_stats.report())
    print()
    print(f"=== Pipeline Summary ===")
    print(f"  Total events:       {num_events:,}")
    print(f"  Handlers completed: {processed_count:,}")
    print(f"  Outbox remaining:   {remaining:,}")
    print(f"  Emit throughput:    {emit_stats.throughput:,.0f} events/sec")
    handler_throughput = processed_count / total_wall if total_wall > 0 else 0
    print(f"  Handler throughput: {handler_throughput:,.0f} events/sec")
    print(f"  Total wall time:   {total_wall:.2f}s")
    print(f"  {pool_snapshot}")

    return emit_stats, handler_stats, StatsCollector("Pipeline Wall")


# ---------------------------------------------------------------------------
# Benchmark 8: Full load (API + emit + relay + worker + handler)
# ---------------------------------------------------------------------------

# Module-level state for the full-load handler (same pattern as pipeline).
_fullload_counter: int = 0
_fullload_target: int = 0
_fullload_done: asyncio.Event | None = None
_fullload_lock: asyncio.Lock | None = None
_fullload_handler_stats: StatsCollector | None = None


async def _fullload_handler(event: "Event", session: AsyncSession) -> None:
    """Handler for full-load benchmark: INSERT into processed_event + track completion."""
    global _fullload_counter

    from app.db.models.processed_event import ProcessedEvent

    session.add(
        ProcessedEvent(
            event_id=event.event_id,
            handler_group="benchmark",
        )
    )

    if _fullload_handler_stats is not None:
        event_created = event.timestamp.timestamp()
        now = time.time()
        _fullload_handler_stats.record(now - event_created)

    if _fullload_lock is not None and _fullload_done is not None:
        async with _fullload_lock:
            _fullload_counter += 1
            if _fullload_counter >= _fullload_target:
                _fullload_done.set()


async def bench_full_load(
    num_requests: int,
    concurrency: int,
    event_load_rate: int,
    pool_size: int = 10,
    batch_size: int = 100,
    api_port: int = 8001,
) -> tuple[StatsCollector, StatsCollector, StatsCollector]:
    """Measure API latency while the full event pipeline runs simultaneously.

    This is the most complete production simulation. All five workloads
    compete for the same PostgreSQL instance:

    1. HTTP clients hammering the API
    2. Background emitters writing events to the outbox
    3. ``OutboxRelay.run()`` with TaskGroup + LISTEN/NOTIFY
    4. ``EventWorker.run()`` consuming from RabbitMQ
    5. Handler doing real DB work (INSERT into processed_event)

    **Requires ``--broker rabbitmq``** and a running API server.

    The benchmark process uses its own DB pool (separate from the server's),
    but they share the same PostgreSQL backend, so DB-level contention
    (row locks, WAL, connection limits) is realistic.

    Metrics:
    - API latency (P50/P95/P99)
    - Background emit throughput
    - Handler throughput and E2E latency
    - Total events emitted and processed during the HTTP benchmark window
    """
    global _fullload_counter, _fullload_target, _fullload_done
    global _fullload_lock, _fullload_handler_stats

    import httpx

    from app.events.broker import RabbitMQBroker
    from app.events.handlers import handler_registry
    from app.events.outbox import emit_event
    from app.events.relay import OutboxRelay
    from app.events.worker import EventWorker

    engine = await create_bench_engine(pool_size=pool_size, max_overflow=pool_size * 2)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Cleanup from previous runs
    await cleanup_outbox(session_factory)
    await cleanup_processed_events(session_factory)

    # Register benchmark handler dynamically
    handler_registry.register(
        event_pattern="benchmark.event",
        group="benchmark",
        handler=_fullload_handler,
    )

    # Create broker
    broker = await create_broker("rabbitmq")
    assert isinstance(broker, RabbitMQBroker), (
        "Full-load benchmark requires RabbitMQBroker"
    )

    # Purge leftover messages
    channel = broker.channel
    try:
        queue = await channel.declare_queue(
            "handlers.benchmark", durable=True, passive=True
        )
        purge_result = await queue.purge()
        purge_count = getattr(purge_result, "message_count", purge_result)
        if purge_count:
            print(f"  Purged {purge_count} leftover messages from handlers.benchmark")
    except Exception:
        pass

    # Initialize handler synchronization state.
    # target is set high — we don't know exactly how many events the
    # background emitters will produce.  We'll check the actual count later.
    _fullload_counter = 0
    _fullload_target = 2**63  # effectively infinite — we stop by time, not count
    _fullload_done = asyncio.Event()
    _fullload_lock = asyncio.Lock()
    _fullload_handler_stats = StatsCollector("Handler E2E Latency")

    # Create services
    relay = OutboxRelay(
        broker=broker,
        session_factory=session_factory,
        batch_size=batch_size,
        poll_interval=1.0,
    )

    worker = EventWorker(
        broker=broker,
        session_factory=session_factory,
        groups={"benchmark"},
    )

    api_stats = StatsCollector("API Latency")
    emit_stats = StatsCollector("Background Emit")
    stop_bg = asyncio.Event()

    num_emitters = min(concurrency, 5)
    print(f"  Broker: rabbitmq")
    print(f"  Background: {num_emitters} emitters @ {event_load_rate} events/sec each")
    print(f"  Background: OutboxRelay.run() + EventWorker.run()")
    print(f"  Handler: INSERT into processed_event (real DB work)")
    print(f"  HTTP concurrency: {concurrency}")

    # Background event emitter (throttled to target rate)
    async def event_emitter():
        seq = 0
        while not stop_bg.is_set():
            event = BenchmarkEvent(source="bench:full-load", seq=seq)
            try:
                async with session_factory() as session:
                    with emit_stats.measure():
                        await emit_event(event, session)
                        await session.commit()
                seq += 1
            except Exception:
                emit_stats.record_error()

            if event_load_rate > 0:
                await asyncio.sleep(1.0 / event_load_rate)

    # HTTP client workers
    async def http_worker(client: httpx.AsyncClient, count: int):
        for _ in range(count):
            with api_stats.measure():
                try:
                    resp = await client.get("/")
                    if resp.status_code != 200:
                        api_stats.record_error()
                except Exception:
                    api_stats.record_error()

    # Start relay and worker
    relay_task = asyncio.create_task(relay.run(), name="bench-fullload-relay")
    worker_task = asyncio.create_task(worker.run(), name="bench-fullload-worker")

    # Give worker time to set up queues and start consuming
    await asyncio.sleep(1.0)

    # Start background emitters
    emit_stats.start_clock()
    _fullload_handler_stats.start_clock()
    bg_emitters = [asyncio.create_task(event_emitter()) for _ in range(num_emitters)]

    # Wait for background load to ramp up
    await asyncio.sleep(0.5)

    # Run HTTP benchmark
    base_url = f"http://127.0.0.1:{api_port}"
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        # Connectivity check
        try:
            resp = await client.get("/")
            if resp.status_code != 200:
                print(
                    f"  WARNING: API returned {resp.status_code}. Is the server running?"
                )
                stop_bg.set()
                for t in bg_emitters:
                    t.cancel()
                relay_task.cancel()
                worker_task.cancel()
                await broker.stop()
                await engine.dispose()
                handler_registry._handlers = [
                    h for h in handler_registry._handlers if h.group != "benchmark"
                ]
                return api_stats, emit_stats, _fullload_handler_stats
        except httpx.ConnectError:
            print(
                f"  ERROR: Cannot connect to {base_url}\n"
                f"  Start the server first: uv run uvicorn app.main:app --port {api_port}"
            )
            stop_bg.set()
            for t in bg_emitters:
                t.cancel()
            relay_task.cancel()
            worker_task.cancel()
            await broker.stop()
            await engine.dispose()
            handler_registry._handlers = [
                h for h in handler_registry._handlers if h.group != "benchmark"
            ]
            return api_stats, emit_stats, _fullload_handler_stats

        api_stats.start_clock()
        per_worker = num_requests // concurrency
        remainder = num_requests % concurrency
        workers = []
        for i in range(concurrency):
            count = per_worker + (1 if i < remainder else 0)
            workers.append(asyncio.create_task(http_worker(client, count)))
        await asyncio.gather(*workers)

    # Stop background emitters
    stop_bg.set()
    for t in bg_emitters:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    total_emitted = emit_stats.count
    print(f"  HTTP phase complete. {total_emitted:,} events emitted during window.")

    # Let the pipeline drain: wait for handler to catch up with what was emitted.
    # Set the real target now that we know how many events were emitted.
    async with _fullload_lock:
        _fullload_target = total_emitted
        if _fullload_counter >= _fullload_target:
            _fullload_done.set()

    drain_timeout = max(30, total_emitted * 0.1)
    print(
        f"  Waiting for handler to drain "
        f"({_fullload_counter:,}/{total_emitted:,} done, "
        f"timeout={drain_timeout:.0f}s)..."
    )
    try:
        await asyncio.wait_for(_fullload_done.wait(), timeout=drain_timeout)
    except asyncio.TimeoutError:
        print(
            f"  WARNING: Drain timed out! "
            f"Processed {_fullload_counter:,}/{total_emitted:,} events"
        )

    # Brief pause to let final commit complete
    await asyncio.sleep(0.5)

    handler_stats = _fullload_handler_stats
    processed_by_handler = _fullload_counter

    # Stop services
    relay_task.cancel()
    worker_task.cancel()
    for task in (relay_task, worker_task):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    pool_snapshot = PoolStats.from_engine(engine.sync_engine)

    # Verify processed_event count
    from app.db.models.processed_event import ProcessedEvent

    async with session_factory() as session:
        result = await session.execute(
            select(func.count(ProcessedEvent.id)).where(
                ProcessedEvent.handler_group == "benchmark"
            )
        )
        db_processed_count = result.scalar_one()

    remaining = await count_pending(session_factory)

    # Cleanup
    await cleanup_outbox(session_factory)
    await cleanup_processed_events(session_factory)

    handler_registry._handlers = [
        h for h in handler_registry._handlers if h.group != "benchmark"
    ]

    await broker.stop()
    await engine.dispose()

    # Reset module-level state
    _fullload_counter = 0
    _fullload_target = 0
    _fullload_done = None
    _fullload_lock = None
    _fullload_handler_stats = None

    # Report
    print()
    print(api_stats.report())
    print()
    print(emit_stats.report())
    emit_event_rate = (
        emit_stats.count / emit_stats.elapsed if emit_stats.elapsed > 0 else 0
    )
    print(f"  Emit events/sec: {emit_event_rate:,.0f}")
    print()
    print(handler_stats.report())
    handler_throughput = (
        db_processed_count / handler_stats.elapsed if handler_stats.elapsed > 0 else 0
    )
    print(f"  Handler events/sec: {handler_throughput:,.0f}")
    print()
    print(f"=== Full-Load Summary ===")
    print(f"  HTTP requests:      {api_stats.count:,} ({api_stats.error_count} errors)")
    print(f"  API P50:            {api_stats.p50:.2f} ms")
    print(f"  API P95:            {api_stats.p95:.2f} ms")
    print(f"  API P99:            {api_stats.p99:.2f} ms")
    print(f"  Events emitted:     {total_emitted:,}")
    print(f"  Handlers completed: {db_processed_count:,}")
    print(f"  Outbox remaining:   {remaining:,}")
    print(f"  Emit throughput:    {emit_event_rate:,.0f} events/sec")
    print(f"  Handler throughput: {handler_throughput:,.0f} events/sec")
    print(f"  {pool_snapshot}")

    return api_stats, emit_stats, handler_stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_benchmarks(args: argparse.Namespace):
    """Run selected benchmarks."""
    benchmarks = args.bench or ["outbox", "relay", "e2e", "concurrent"]

    broker_kind: BrokerKind = args.broker

    print("=" * 60)
    print("  Event System Benchmarks")
    print(f"  Events: {args.events:,}  Concurrency: {args.concurrency}")
    print(f"  Pool size: {args.pool_size}  Batch size: {args.batch_size}")
    print(f"  Broker: {broker_kind}")
    print("=" * 60)
    print()

    if "outbox" in benchmarks:
        print("--- Benchmark 1: Outbox Write Throughput ---")
        await bench_outbox_write(
            num_events=args.events,
            concurrency=args.concurrency,
            pool_size=args.pool_size,
        )
        print()

    if "relay" in benchmarks:
        print("--- Benchmark 2: Relay Throughput ---")
        await bench_relay(
            num_events=args.events,
            batch_size=args.batch_size,
            pool_size=args.pool_size,
            broker_kind=broker_kind,
        )
        print()

    if "api" in benchmarks:
        print(f"--- Benchmark 3: API Latency Under Emit+Relay Load ---")
        print(f"  (Requires server running on :{args.api_port})")
        await bench_api_latency(
            num_requests=args.events,
            concurrency=args.concurrency,
            event_load_rate=args.event_rate,
            pool_size=args.pool_size,
            batch_size=args.batch_size,
            broker_kind=broker_kind,
            api_port=args.api_port,
        )
        print()

    if "e2e" in benchmarks:
        print("--- Benchmark 4: End-to-End Pipeline ---")
        await bench_end_to_end(
            num_events=args.events,
            concurrency=args.concurrency,
            pool_size=args.pool_size,
            broker_kind=broker_kind,
        )
        print()

    if "concurrent" in benchmarks:
        print("--- Benchmark 5: Concurrent Emit + Relay ---")
        await bench_concurrent(
            num_events=args.events,
            concurrency=args.concurrency,
            pool_size=args.pool_size,
            batch_size=args.batch_size,
            broker_kind=broker_kind,
        )
        print()

    if "relay_lifecycle" in benchmarks:
        print("--- Benchmark 6: Relay Lifecycle (run()) ---")
        await bench_relay_lifecycle(
            num_events=args.events,
            batch_size=args.batch_size,
            pool_size=args.pool_size,
            broker_kind=broker_kind,
        )
        print()

    if "pipeline" in benchmarks:
        print("--- Benchmark 7: Full Pipeline (emit → relay → worker → handler) ---")
        await bench_pipeline(
            num_events=args.events,
            concurrency=args.concurrency,
            pool_size=args.pool_size,
            batch_size=args.batch_size,
            broker_kind=broker_kind,
        )
        print()

    if "full_load" in benchmarks:
        print("--- Benchmark 8: Full Load (API + emit + relay + worker + handler) ---")
        print(f"  (Requires server running on :{args.api_port} and RabbitMQ)")
        await bench_full_load(
            num_requests=args.events,
            concurrency=args.concurrency,
            event_load_rate=args.event_rate,
            pool_size=args.pool_size,
            batch_size=args.batch_size,
            api_port=args.api_port,
        )
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the event-driven architecture"
    )
    parser.add_argument(
        "--bench",
        nargs="+",
        choices=[
            "outbox",
            "relay",
            "api",
            "e2e",
            "concurrent",
            "relay_lifecycle",
            "pipeline",
            "full_load",
        ],
        help="Which benchmarks to run (default: outbox, relay, e2e, concurrent)",
    )
    parser.add_argument(
        "--broker",
        choices=["memory", "rabbitmq"],
        default="memory",
        help="Broker backend for relay/e2e/concurrent benchmarks (default: memory)",
    )
    parser.add_argument(
        "--events",
        type=int,
        default=5000,
        help="Number of events to emit (default: 5000)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent workers (default: 10)",
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=10,
        help="DB connection pool size (default: 10)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Relay batch size (default: 100)",
    )
    parser.add_argument(
        "--event-rate",
        type=int,
        default=500,
        help="Background event rate for API benchmark (events/sec, default: 500)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8001,
        help="Port for the API server (default: 8001)",
    )

    args = parser.parse_args()
    asyncio.run(run_benchmarks(args))


if __name__ == "__main__":
    main()
