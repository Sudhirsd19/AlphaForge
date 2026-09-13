"""
AlphaForge Lightweight In-Process Metrics Registry.

Provides thread-safe counters, gauges, and monotonic latency measurements.
Metrics are strictly diagnostic (READ/RECORD) and cannot alter or influence trading decisions.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class MetricsRegistry:
    """
    Lightweight, thread-safe, in-process metrics collector for personal trading installations.

    Invariants:
    - Counters are strictly deterministic integers incremented during operations.
    - Durations are measured using time.perf_counter() (monotonic runtime measurements).
    - Metrics state is strictly read-only for domain logic: no trading decision may read them.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {
            # DATA metrics
            "data.events_received": 0,
            "data.data_gaps": 0,
            "data.backfills": 0,
            "data.stale_data_events": 0,
            # STRATEGY metrics
            "strategy.signals_generated": 0,
            "strategy.signals_accepted": 0,
            "strategy.signals_rejected": 0,
            # RISK metrics
            "risk.risk_checks": 0,
            "risk.risk_rejections": 0,
            "risk.risk_reservations": 0,
            "risk.risk_releases": 0,
            "risk.circuit_breaker_events": 0,
            # ORDER metrics
            "order.orders_submitted": 0,
            "order.orders_acknowledged": 0,
            "order.orders_rejected": 0,
            "order.orders_timed_out": 0,
            "order.orders_retried": 0,
            "order.orders_cancelled": 0,
            # FILL metrics
            "fill.fills_received": 0,
            # RECONCILIATION metrics
            "reconciliation.reconciliation_runs": 0,
            "reconciliation.reconciliation_failures": 0,
            "reconciliation.reconciliation_mismatches": 0,
            # OBSERVABILITY metrics
            "observability.events_emitted": 0,
            "observability.events_dropped": 0,
            "observability.sink_failures": 0,
            "observability.dispatcher_failures": 0,
        }
        self._gauges: dict[str, float] = {}
        self._latencies: dict[str, list[float]] = {
            "data.data_latency": [],
            "fill.fill_latency": [],
        }
        self._lock = threading.RLock()

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter deterministically by value."""
        if value < 0:
            raise ValueError(f"Increment value must be non-negative, got {value}")
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge to a specific floating-point or integer value."""
        with self._lock:
            self._gauges[name] = float(value)

    def record_latency(self, name: str, duration_sec: float) -> None:
        """Record a measured duration in seconds (monotonic clock)."""
        if duration_sec < 0:
            raise ValueError(f"Duration cannot be negative, got {duration_sec}")
        with self._lock:
            if name not in self._latencies:
                self._latencies[name] = []
            self._latencies[name].append(float(duration_sec))

    @contextmanager
    def measure_latency(self, name: str) -> Iterator[None]:
        """Measure the execution time of a block using monotonic time.perf_counter()."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.record_latency(name, elapsed)

    def get_counter(self, name: str) -> int:
        """Read current counter value."""
        with self._lock:
            return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float | None:
        """Read current gauge value."""
        with self._lock:
            return self._gauges.get(name)

    def get_latencies(self, name: str) -> list[float]:
        """Read recorded durations for a given latency metric."""
        with self._lock:
            return list(self._latencies.get(name, []))

    def reset(self) -> None:
        """Reset all metrics to initial baseline state (useful in test isolation)."""
        with self._lock:
            for k in self._counters:
                self._counters[k] = 0
            self._gauges.clear()
            for k in self._latencies:
                self._latencies[k] = []

    def snapshot(self) -> dict[str, Any]:
        """Return an immutable snapshot of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "latencies_count": {k: len(v) for k, v in self._latencies.items()},
                "latencies_avg": {
                    k: (sum(v) / len(v) if v else 0.0) for k, v in self._latencies.items()
                },
            }
