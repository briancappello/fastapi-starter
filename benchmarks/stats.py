"""Shared statistics collection and reporting for benchmarks.

Provides a StatsCollector that records individual operation latencies,
computes percentiles, and prints formatted reports. Thread-safe for
concurrent async usage.
"""

from __future__ import annotations

import statistics
import time

from dataclasses import dataclass, field


@dataclass
class StatsCollector:
    """Collects timing samples and computes summary statistics.

    Usage::

        stats = StatsCollector("emit_event")
        with stats.measure():
            await emit_event(event, session)

        stats.report()
    """

    name: str
    _samples: list[float] = field(default_factory=list)
    _errors: int = 0
    _start_time: float | None = None

    def start_clock(self) -> None:
        """Mark the beginning of the benchmark run."""
        self._start_time = time.monotonic()

    @property
    def elapsed(self) -> float:
        """Wall-clock seconds since start_clock()."""
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    def record(self, duration_seconds: float) -> None:
        """Record a single operation's duration."""
        self._samples.append(duration_seconds)

    def record_error(self) -> None:
        """Record a failed operation."""
        self._errors += 1

    class _Timer:
        """Context manager that records elapsed time to a StatsCollector."""

        def __init__(self, collector: StatsCollector):
            self._collector = collector
            self._start: float = 0.0

        def __enter__(self):
            self._start = time.monotonic()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            elapsed = time.monotonic() - self._start
            if exc_type is None:
                self._collector.record(elapsed)
            else:
                self._collector.record_error()
            return False  # Don't suppress exceptions

    def measure(self) -> _Timer:
        """Return a context manager that records the enclosed duration.

        Usage::

            with stats.measure():
                await some_operation()
        """
        return self._Timer(self)

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def error_count(self) -> int:
        return self._errors

    @property
    def total_ops(self) -> int:
        return self.count + self._errors

    @property
    def throughput(self) -> float:
        """Operations per second (based on wall-clock time)."""
        if self.elapsed <= 0:
            return 0.0
        return self.count / self.elapsed

    def percentile(self, p: float) -> float:
        """Return the p-th percentile latency in milliseconds."""
        if not self._samples:
            return 0.0
        sorted_samples = sorted(self._samples)
        idx = int(len(sorted_samples) * p / 100)
        idx = min(idx, len(sorted_samples) - 1)
        return sorted_samples[idx] * 1000  # Convert to ms

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def mean_ms(self) -> float:
        if not self._samples:
            return 0.0
        return statistics.mean(self._samples) * 1000

    @property
    def min_ms(self) -> float:
        if not self._samples:
            return 0.0
        return min(self._samples) * 1000

    @property
    def max_ms(self) -> float:
        if not self._samples:
            return 0.0
        return max(self._samples) * 1000

    def report(self) -> str:
        """Format a summary report as a string."""
        lines = [
            f"=== {self.name} ===",
            f"  Total ops:    {self.total_ops:,}  "
            f"({self.count:,} ok, {self._errors:,} errors)",
            f"  Wall time:    {self.elapsed:.2f}s",
            f"  Throughput:   {self.throughput:,.0f} ops/sec",
            f"  Latency:",
            f"    min:  {self.min_ms:8.2f} ms",
            f"    p50:  {self.p50:8.2f} ms",
            f"    p95:  {self.p95:8.2f} ms",
            f"    p99:  {self.p99:8.2f} ms",
            f"    max:  {self.max_ms:8.2f} ms",
            f"    mean: {self.mean_ms:8.2f} ms",
        ]
        return "\n".join(lines)


def print_report(*collectors: StatsCollector) -> None:
    """Print reports for multiple collectors with a separator."""
    for i, c in enumerate(collectors):
        if i > 0:
            print()
        print(c.report())


@dataclass
class PoolStats:
    """Snapshot of SQLAlchemy connection pool state."""

    pool_size: int
    checked_out: int
    overflow: int
    checked_in: int

    @classmethod
    def from_engine(cls, engine) -> PoolStats:
        """Capture current pool state from a SQLAlchemy engine."""
        pool = engine.pool
        return cls(
            pool_size=pool.size(),
            checked_out=pool.checkedout(),
            overflow=pool.overflow(),
            checked_in=pool.checkedin(),
        )

    def __str__(self) -> str:
        return (
            f"Pool: {self.checked_out} checked out, "
            f"{self.checked_in} checked in, "
            f"{self.overflow} overflow "
            f"(size={self.pool_size})"
        )
