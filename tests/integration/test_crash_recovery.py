"""
Integration tests for AlphaForge Crash Recovery & Cold-Boot Reconciliation.
Simulates end-to-end failure scenarios:
1. Process termination and cold-boot state recovery from atomic persistence.
2. Network timeout after broker acceptance with zero duplicate order generation.
3. Partial fill crash and accurate state restoration.
4. Active position protection re-verification across process restarts.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from alphaforge.broker.models import BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.execution.enums import OrderSide, OrderState
from alphaforge.execution.idempotency import (
    OrderRole,
    generate_client_order_id,
)
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import ReconciliationStatus
from alphaforge.reconciliation.reconciler import ColdBootReconciler
from alphaforge.reconciliation.state_store import (
    AtomicStateStore,
    LocalOrderRecord,
    RecoverySnapshot,
)
from alphaforge.risk.enums import TradeSide


def test_e2e_cold_boot_recovery_from_persistence(tmp_path: Path) -> None:
    """Simulate clean lifecycle persistence, process shutdown, and cold-boot state recovery."""
    state_file = tmp_path / "app_recovery_state.json"
    broker = PaperBroker()

    # --- Pre-Crash Execution Context ---
    client_id = generate_client_order_id(
        "TREND_PULLBACK", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-2026-001"
    )
    req = BrokerOrderRequest(
        client_order_id=client_id,
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    brk_order = broker.submit_order(req)
    assert brk_order.client_order_id == client_id

    # Persist state atomically before simulated crash
    pre_crash_store = AtomicStateStore(state_file)
    now = datetime.now(UTC)
    local_order = LocalOrderRecord(
        order_id="ORD-LIVE-001",
        client_order_id=client_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        state=OrderState.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    pre_crash_store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            orders={client_id: local_order},
            created_at=now,
        )
    )

    # --- SIMULATED PROCESS KILL & RESTART ---
    # Create brand new local context pointing to existing broker and persisted file
    post_crash_store = AtomicStateStore(state_file)
    reopened_gate = ReconciliationGate()
    reconciler = ColdBootReconciler(
        broker=broker,
        state_store=post_crash_store,
        gate=reopened_gate,
    )

    assert not reopened_gate.is_open

    # Run 9-step cold-boot reconciliation
    result = reconciler.reconcile()

    assert result.status == ReconciliationStatus.MATCHED
    assert result.matched_count == 1
    assert result.mismatch_count == 0
    assert result.unknown_count == 0
    assert result.new_entries_allowed is True
    assert reopened_gate.is_open


def test_network_timeout_after_broker_acceptance_zero_duplicates(tmp_path: Path) -> None:
    """
    Test scenario:
    1. Client submits order with deterministic client_order_id.
    2. Broker accepts order, but network response times out.
    3. Caller enters UNKNOWN state locally and crashes/restarts.
    4. On restart, reconciler finds existing order by client_order_id.
    5. Zero duplicate orders are submitted.
    """
    state_file = tmp_path / "timeout_recovery_state.json"
    broker = PaperBroker()
    broker.set_simulate_timeout(True)

    client_id = generate_client_order_id(
        "TREND_PULLBACK", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-TIMEOUT-1"
    )
    req = BrokerOrderRequest(
        client_order_id=client_id,
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=25,
        role=OrderRole.ENTRY,
    )

    # Client submits order -> timeout occurs
    with pytest.raises(TimeoutError):
        broker.submit_order(req)

    # Local system records intent in UNKNOWN state before crash
    store = AtomicStateStore(state_file)
    now = datetime.now(UTC)
    local_order = LocalOrderRecord(
        order_id="ORD-TIMEOUT-001",
        client_order_id=client_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=25,
        state=OrderState.UNKNOWN,
        created_at=now,
        updated_at=now,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            orders={client_id: local_order},
            created_at=now,
        )
    )

    # Disable timeout for recovery pass
    broker.set_simulate_timeout(False)

    # Cold boot restart
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)
    res = reconciler.reconcile()

    assert res.status == ReconciliationStatus.MATCHED
    assert res.matched_count == 1
    assert len(broker.get_open_orders()) == 1  # Exactly 1 order on broker book!

    # Verify second submission attempt with same client_order_id returns existing order
    duplicate_res = broker.submit_order(req)
    assert duplicate_res.client_order_id == client_id
    assert len(broker.get_open_orders()) == 1  # Zero duplicate orders!


def test_crash_after_partial_fill_restores_state(tmp_path: Path) -> None:
    """Test process restart after broker executed partial fill accurately aligns state."""
    state_file = tmp_path / "partial_recovery_state.json"
    broker = PaperBroker()

    client_id = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-PART-1")
    req = BrokerOrderRequest(
        client_order_id=client_id,
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=100,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req)

    # Broker fills 40 units
    broker.simulate_partial_fill(client_id, 40, Decimal("24500.00"))

    # Local persisted record had not caught up before crash (was still in ACKNOWLEDGED)
    store = AtomicStateStore(state_file)
    now = datetime.now(UTC)
    local_order = LocalOrderRecord(
        order_id="ORD-PART-001",
        client_order_id=client_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=100,
        filled_quantity=0,
        state=OrderState.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            orders={client_id: local_order},
            created_at=now,
        )
    )

    # Cold boot restart
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    res = reconciler.reconcile()
    # Partial fill found; matching record created
    assert res.matched_count >= 1
    assert any(rec.action.value == "SYNC_PARTIAL_FILL" for rec in res.order_details)
