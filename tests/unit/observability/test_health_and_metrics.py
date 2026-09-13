"""
Unit tests for Phase 14 Health Models and Metrics Registry.

Covers:
- OBS15: Health status rollup priority (FAILED > BLOCKED > DEGRADED > HEALTHY).
- OBS16: Deterministic metrics increments and snapshots.
- OBS17: Monotonic latency measurements using time.perf_counter().
- Health diagnostic-only isolation invariant.
"""

from __future__ import annotations

import time

from alphaforge.observability.health import HealthAggregator, HealthStatus
from alphaforge.observability.metrics import MetricsRegistry


def test_obs15_health_aggregation_rollup_priority() -> None:
    """
    OBS15: Health rollup priority follows strict severity order:
    FAILED > BLOCKED > DEGRADED > HEALTHY.
    """
    aggregator = HealthAggregator()

    # Empty aggregator defaults to HEALTHY
    assert aggregator.compute_snapshot().overall_status == HealthStatus.HEALTHY

    # 1. All healthy
    aggregator.update_component("market_data", HealthStatus.HEALTHY, "Receiving ticks")
    aggregator.update_component("strategy", HealthStatus.HEALTHY, "Engine ready")
    assert aggregator.compute_snapshot().overall_status == HealthStatus.HEALTHY

    # 2. Add DEGRADED component -> overall becomes DEGRADED
    aggregator.update_component("broker", HealthStatus.DEGRADED, "Elevated latency")
    assert aggregator.compute_snapshot().overall_status == HealthStatus.DEGRADED

    # 3. Add BLOCKED component -> overall becomes BLOCKED (BLOCKED > DEGRADED)
    aggregator.update_component("security", HealthStatus.BLOCKED, "Kill switch engaged")
    assert aggregator.compute_snapshot().overall_status == HealthStatus.BLOCKED

    # 4. Add FAILED component -> overall becomes FAILED (FAILED > BLOCKED)
    aggregator.update_component("persistence", HealthStatus.FAILED, "Disk write error")
    assert aggregator.compute_snapshot().overall_status == HealthStatus.FAILED

    # Snapshot invariants
    snap = aggregator.compute_snapshot()
    assert snap.is_degraded is True
    assert "persistence" in snap.components
    assert snap.components["persistence"].status == HealthStatus.FAILED


def test_obs16_metrics_increments_and_snapshots() -> None:
    """OBS16: Metrics increment deterministically, update gauges, and snapshot safely."""
    registry = MetricsRegistry()

    # Initial baseline
    assert registry.get_counter("order.orders_submitted") == 0

    # Deterministic increments
    registry.increment("order.orders_submitted", 1)
    registry.increment("order.orders_submitted", 4)
    assert registry.get_counter("order.orders_submitted") == 5

    # Gauges
    registry.set_gauge("system.cpu_usage", 12.5)
    assert registry.get_gauge("system.cpu_usage") == 12.5

    # Snapshot isolation
    snap = registry.snapshot()
    assert snap["counters"]["order.orders_submitted"] == 5
    assert snap["gauges"]["system.cpu_usage"] == 12.5

    # Reset
    registry.reset()
    assert registry.get_counter("order.orders_submitted") == 0
    assert registry.get_gauge("system.cpu_usage") is None


def test_obs17_monotonic_latency_measurement() -> None:
    """OBS17: Durations use monotonic time.perf_counter() and measure accurately."""
    registry = MetricsRegistry()

    with registry.measure_latency("data.data_latency"):
        # Monotonic busy spin ~1ms (never use sleep)
        start = time.perf_counter()
        while time.perf_counter() - start < 0.001:
            pass

    latencies = registry.get_latencies("data.data_latency")
    assert len(latencies) == 1
    assert latencies[0] >= 0.001  # At least 1ms elapsed
    assert latencies[0] < 1.0  # Well within bounds
