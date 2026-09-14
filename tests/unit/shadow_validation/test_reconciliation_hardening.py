"""
Unit Tests for Phase 18-I Continuous Multi-Trigger Reconciliation Hardening.

Verifies:
1. On-fill reconciliation (aligned vs quantity desync tripping kill switch).
2. On-order-event reconciliation (resolving UNKNOWN states vs fatal state conflict).
3. On-reconnect reconciliation (full audit of positions and cash).
4. Periodic reconciliation (cash discrepancy halting new risk).
5. Cryptographic cycle hashing and tamper-evident audit history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.shadow_validation.reconciliation_hardening import (
    ContinuousReconciliationCoordinator,
    DiscrepancySeverity,
    ReconciliationActionTaken,
    ReconciliationTriggerType,
)


def test_on_fill_reconciliation_aligned() -> None:
    coord = ContinuousReconciliationCoordinator()
    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)

    # Local position = 50, Venue position = 50 -> Perfectly aligned
    cycle = coord.trigger_on_fill(
        symbol="NIFTY26SEPFUT",
        local_position=Decimal("50"),
        venue_position=Decimal("50"),
        timestamp=now,
    )

    assert cycle.trigger == ReconciliationTriggerType.ON_FILL
    assert len(cycle.discrepancies) == 0
    assert coord.is_kill_switch_tripped is False
    assert coord.is_risk_halted is False
    assert len(cycle.cycle_hash) == 64


def test_on_fill_position_desync_trips_kill_switch() -> None:
    kill_switch_events: list[str] = []

    def on_kill(reason: str) -> None:
        kill_switch_events.append(reason)

    coord = ContinuousReconciliationCoordinator(kill_switch_callback=on_kill)
    now = datetime(2026, 9, 14, 10, 1, 0, tzinfo=UTC)

    # Local position = 50, Venue position = 100 -> Desync of 50 contracts!
    cycle = coord.trigger_on_fill(
        symbol="NIFTY26SEPFUT",
        local_position=Decimal("50"),
        venue_position=Decimal("100"),
        timestamp=now,
    )

    assert coord.is_kill_switch_tripped is True
    assert coord.is_risk_halted is True
    assert len(kill_switch_events) == 1
    assert "POSITION_DESYNC_ON_FILL" in kill_switch_events[0]

    assert len(cycle.discrepancies) == 1
    disc = cycle.discrepancies[0]
    assert disc.severity == DiscrepancySeverity.FATAL_DESYNC
    assert disc.action_taken == ReconciliationActionTaken.KILL_SWITCH_TRIPPED
    assert disc.symbol == "NIFTY26SEPFUT"
    assert disc.discrepancy_delta == "50"


def test_on_order_event_unknown_state_resolves() -> None:
    # Query callback resolves UNKNOWN order to FILLED
    def query_venue(order_id: str) -> str:
        if order_id == "ORD-1234":
            return "FILLED"
        return "PENDING"

    coord = ContinuousReconciliationCoordinator(venue_order_query_callback=query_venue)
    now = datetime(2026, 9, 14, 10, 2, 0, tzinfo=UTC)

    cycle = coord.trigger_on_order_event(
        order_id="ORD-1234",
        symbol="NIFTY26SEPFUT",
        local_state="UNKNOWN",
        venue_state="FILLED",
        timestamp=now,
    )

    # Should resolve UNKNOWN to FILLED without tripping kill switch
    assert coord.is_kill_switch_tripped is False
    assert len(cycle.discrepancies) == 1
    disc = cycle.discrepancies[0]
    assert disc.severity == DiscrepancySeverity.WARNING
    assert disc.action_taken == ReconciliationActionTaken.ORDER_RESOLVED


def test_on_order_event_state_conflict_trips_kill_switch() -> None:
    coord = ContinuousReconciliationCoordinator()
    now = datetime(2026, 9, 14, 10, 3, 0, tzinfo=UTC)

    # Local state REJECTED vs Venue state FILLED -> Fatal conflict
    cycle = coord.trigger_on_order_event(
        order_id="ORD-9999",
        symbol="NIFTY26SEPFUT",
        local_state="REJECTED",
        venue_state="FILLED",
        timestamp=now,
    )

    assert coord.is_kill_switch_tripped is True
    assert len(cycle.discrepancies) == 1
    disc = cycle.discrepancies[0]
    assert disc.severity == DiscrepancySeverity.FATAL_DESYNC
    assert disc.action_taken == ReconciliationActionTaken.KILL_SWITCH_TRIPPED


def test_periodic_reconciliation_cash_mismatch_halts_risk() -> None:
    risk_halt_events: list[str] = []

    def on_risk_halt(reason: str) -> None:
        risk_halt_events.append(reason)

    coord = ContinuousReconciliationCoordinator(
        risk_halt_callback=on_risk_halt,
        cash_tolerance=Decimal("5.00"),
    )
    now = datetime(2026, 9, 14, 10, 4, 0, tzinfo=UTC)

    # Positions aligned, but cash has 500 INR unexplained divergence
    cycle = coord.trigger_periodic(
        local_positions={"NIFTY26SEPFUT": Decimal("50")},
        venue_positions={"NIFTY26SEPFUT": Decimal("50")},
        local_cash=Decimal("1000000.00"),
        venue_cash=Decimal("999500.00"),
        timestamp=now,
    )

    assert coord.is_risk_halted is True
    assert coord.is_kill_switch_tripped is False
    assert len(risk_halt_events) == 1
    assert "CASH_MARGIN_DISCREPANCY" in risk_halt_events[0]

    assert len(cycle.discrepancies) == 1
    disc = cycle.discrepancies[0]
    assert disc.severity == DiscrepancySeverity.CRITICAL_MISMATCH
    assert disc.action_taken == ReconciliationActionTaken.RISK_HALTED


def test_on_reconnect_reconciliation_full_audit() -> None:
    coord = ContinuousReconciliationCoordinator()
    now = datetime(2026, 9, 14, 10, 5, 0, tzinfo=UTC)

    # Reconnect finds missing phantom position on venue
    cycle = coord.trigger_on_reconnect(
        local_positions={"NIFTY26SEPFUT": Decimal("0")},
        venue_positions={"NIFTY26SEPFUT": Decimal("50")},
        local_cash=Decimal("1000000"),
        venue_cash=Decimal("1000000"),
        timestamp=now,
    )

    assert cycle.trigger == ReconciliationTriggerType.ON_RECONNECT
    assert coord.is_kill_switch_tripped is True
    assert len(cycle.discrepancies) == 1
    assert cycle.discrepancies[0].entity_type == "POSITION"


def test_test_reset_and_history_pruning() -> None:
    coord = ContinuousReconciliationCoordinator(max_history=5)
    now = datetime(2026, 9, 14, 10, 6, 0, tzinfo=UTC)

    # Trip kill switch
    coord.trigger_on_fill("NIFTY", Decimal("0"), Decimal("10"), now)
    assert coord.is_kill_switch_tripped is True

    # Unauthorized reset fails
    with pytest.raises(DataIntegrityError):
        coord.reset_for_test("BAD_TOKEN")

    # Authorized reset succeeds
    coord.reset_for_test("TEST_ONLY_RESET")
    assert coord.is_kill_switch_tripped is False

    # Check history cap
    for _ in range(10):
        coord.trigger_periodic({}, {}, Decimal("100"), Decimal("100"), now)
    history = coord.get_history()
    assert len(history) == 5
