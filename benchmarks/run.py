"""Benchmark runner for the event-driven architecture.

Measures throughput and latency at each stage of the pipeline:
1. Outbox write throughput (emit_event)
2. Relay throughput (outbox → broker)
3. API latency under event load
4. End-to-end pipeline (emit → relay → handler)

Usage:
    # Run all benchmarks with defaults
    python -m benchmarks.run

    # Run a specific benchmark
    python -m benchmarks.run --bench outbox

    # Customize parameters
    python -m benchmarks.run --events 10000 --concurrency 20

    # Quick smoke test
    python -m benchmarks.run --events 500 --concurrency 5
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

from .stats import PoolStats, StatsCollector, print_report


# ---------------------------------------------------------------------------
# Benchmark event type (defined here to avoid polluting the app registry
# in production — these are only used during benchmarks)
# ---------------------------------------------------------------------------

from app.events.base import Event


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


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the event-driven architecture"
    )
    parser.add_argument(
        "--bench",
        nargs="+",
        choices=["outbox", "relay", "api", "e2e", "concurrent"],
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
