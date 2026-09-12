"""
Unit tests for AlphaForge 9-Step Cold-Boot Reconciler.
Verifies all 6 matching cases (A through F), broker unavailability,
protection re-verification, gate blocking, and Phase 7 FSM synchronization.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
)
from alphaforge.broker.paper import PaperBroker
from alphaforge.execution.enums import OrderSide, OrderState
from alphaforge.execution.idempotency import OrderRole
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    OrderReconciliationRecord,
    ReconciliationAction,
    ReconciliationReasonCode,
    ReconciliationStatus,
)
from alphaforge.reconciliation.reconciler import ColdBootReconciler
from alphaforge.reconciliation.state_store import (
    InMemoryStateStore,
    LocalOrderRecord,
    LocalPositionRecord,
    RecoverySnapshot,
)
from alphaforge.risk.enums import TradeSide


def test_reconciliation_case_a_matching_open_order() -> None:
    """Case A: Local order exists + Broker order exists with matching intent."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)
    client_id = "AF-E-CASE-A"

    # Setup broker order
    req = BrokerOrderRequest(
        client_order_id=client_id,
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req)

    # Setup local persisted snapshot
    order = LocalOrderRecord(
        order_id="ORD-CASE-A",
        client_order_id=client_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        state=OrderState.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            orders={client_id: order},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MATCHED
    assert result.matched_count == 1
    assert result.mismatch_count == 0
    assert result.unknown_count == 0
    assert result.new_entries_allowed is True
    assert gate.is_open


def test_reconciliation_case_b_local_exists_broker_missing() -> None:
    """Case B: Local order exists in submitted state, but broker has zero record."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)
    client_id = "AF-E-CASE-B"

    # Local order was submitted, but broker has no record
    order = LocalOrderRecord(
        order_id="ORD-CASE-B",
        client_order_id=client_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        state=OrderState.SUBMITTED,
        created_at=now,
        updated_at=now,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            orders={client_id: order},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.ESCALATED
    assert result.unknown_count == 1
    assert result.new_entries_allowed is False
    assert not gate.is_open


def test_reconciliation_case_c_broker_order_exists_local_missing() -> None:
    """Case C: Broker order exists without any local record (UNKNOWN_EXTERNAL_ORDER)."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # External order in broker
    ext_order = BrokerOrder(
        broker_order_id="BRK-EXT-1",
        client_order_id="AF-E-EXT-1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    broker.inject_external_order(ext_order)

    # Empty local snapshot
    store.save_snapshot(RecoverySnapshot(schema_version=1, created_at=now))

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.ESCALATED
    assert result.reason_code == ReconciliationReasonCode.UNKNOWN_EXTERNAL_ORDER
    assert result.unknown_count == 1
    assert not result.new_entries_allowed
    assert not gate.is_open


def test_reconciliation_case_d_matching_position() -> None:
    """Case D: Local position matches broker position and resting protection is confirmed."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Broker position 50 NIFTY LONG
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Resting stop on broker
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-1",
            client_order_id="AF-S-STOP-1",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    # Local position
    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MATCHED
    assert result.matched_count == 1
    assert result.new_entries_allowed is True
    assert gate.is_open


def test_reconciliation_case_e_position_mismatch() -> None:
    """Case E: Local position quantity disagrees with broker position quantity."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Broker reports 100
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=100,  # 100 at broker
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )

    # Local reports 50
    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,  # 50 locally!
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MISMATCH
    assert result.reason_code == ReconciliationReasonCode.POSITION_MISMATCH
    assert result.mismatch_count >= 1
    assert not result.new_entries_allowed
    assert not gate.is_open


def test_reconciliation_case_f_unknown_external_position() -> None:
    """Case F: Broker has open position, but local records are empty (UNKNOWN_EXTERNAL_POSITION)."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Broker has 50 NIFTY
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-EXT",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )

    # Local has zero positions
    store.save_snapshot(RecoverySnapshot(schema_version=1, created_at=now))

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.ESCALATED
    assert result.reason_code == ReconciliationReasonCode.UNKNOWN_EXTERNAL_POSITION
    assert result.unknown_count == 1
    assert not result.new_entries_allowed
    assert not gate.is_open


def test_reconciliation_protection_unconfirmed_hazard() -> None:
    """Active position without confirmed resting stop fails reconciliation and locks gate."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Position exists at broker
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # NO resting stop on broker!

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=False,  # Unprotected!
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MISMATCH
    assert result.reason_code == ReconciliationReasonCode.PROTECTION_UNCONFIRMED
    assert not result.new_entries_allowed
    assert not gate.is_open


def test_reconciliation_broker_unavailable() -> None:
    """When broker is unavailable, reconciliation returns FAILED and blocks entries."""
    broker = PaperBroker()
    broker.set_available(False)
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.FAILED
    assert result.reason_code == ReconciliationReasonCode.BROKER_UNAVAILABLE
    assert not result.new_entries_allowed
    assert not gate.is_open


def test_reconciler_align_fsm() -> None:
    """Test align_fsm synchronizing Phase 7 FSM through RECONCILING to target states."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    # Order in UNKNOWN
    fsm = OrderStateMachine(
        order_id="ORD-FSM-ALIGN",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=25,
        initial_state=OrderState.UNKNOWN,
    )
    assert fsm.current_state == OrderState.UNKNOWN

    record = OrderReconciliationRecord(
        client_order_id="AF-E-1",
        broker_order_id="BRK-1",
        local_state=OrderState.UNKNOWN,
        broker_status=BrokerOrderStatus.ACKNOWLEDGED,
        action=ReconciliationAction.SYNC_ACKNOWLEDGED,
        reason_code=ReconciliationReasonCode.MATCHED,
    )
    reconciler.align_fsm(fsm, record)
    aligned_state: OrderState = fsm.current_state
    assert aligned_state == OrderState.ACKNOWLEDGED


def test_reconciliation_stale_local_true_flag_no_broker_stop() -> None:
    """Finding 1 Test A: Stale local is_protected=True must NOT bypass missing broker stop."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Broker has open position 50 NIFTY LONG, but ZERO resting stops
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )

    # Local position falsely claims is_protected=True
    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert result.manual_escalation_required is True
    assert gate.is_open is False
    assert result.mismatch_count >= 1
    # Verify emergency protection hazard record was created
    assert any(
        rec.action == ReconciliationAction.TRIGGER_EMERGENCY_PROTECTION
        and rec.reason_code == ReconciliationReasonCode.PROTECTION_UNCONFIRMED
        for rec in result.order_details
    )


def test_reconciliation_local_false_valid_broker_stop() -> None:
    """Finding 1 Test B: Local is_protected=False is overridden by authoritative broker stop."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Broker position 50 NIFTY LONG
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Valid resting stop on broker
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-1",
            client_order_id="AF-S-STOP-1",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    # Local position has is_protected=False
    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=False,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is True
    assert gate.is_open is True
    assert result.position_details[0].is_protection_confirmed is True


def test_reconciliation_wrong_stop_direction() -> None:
    """Finding 1 Test C: Stop with wrong side (BUY for LONG) is not valid protection."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Wrong direction: BUY stop for LONG position
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-WRONG-SIDE",
            client_order_id="AF-S-WRONG-SIDE",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False


def test_reconciliation_partial_stop_quantity() -> None:
    """Finding 1 Test D: Stop with partial quantity (25 for 50) is not fully protected."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Partial quantity: 25 instead of 50
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-PARTIAL",
            client_order_id="AF-S-PARTIAL",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=25,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False


def test_reconciliation_wrong_symbol_stop() -> None:
    """Finding 1 Test E: Stop for different symbol (BANKNIFTY for NIFTY) is not valid."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Wrong symbol: BANKNIFTY stop
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-WRONG-SYM",
            client_order_id="AF-S-WRONG-SYM",
            symbol="BANKNIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False


def test_reconciliation_cancelled_or_rejected_stop() -> None:
    """Finding 1 Test F: Cancelled or rejected stop order is not valid protection."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Cancelled stop order
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-CANC",
            client_order_id="AF-S-CANC",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.CANCELLED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False


def test_reconciliation_valid_resting_stop() -> None:
    """Finding 1 Test G: Full matching resting stop validates protection and opens gate."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-VALID",
            client_order_id="AF-S-VALID",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=False,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status == ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is True
    assert gate.is_open is True


def test_reconciliation_known_protection_id_missing_unrelated_stop_exists() -> None:
    """
    Finding 1 Test 1: Known protection ID missing + unrelated valid stop exists
    -> protection remains unconfirmed.
    """
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Broker has a valid-looking stop on NIFTY, 50, SELL, but unrelated client_order_id
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-UNRELATED",
            client_order_id="AF-S-UNRELATED",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    # Local position specifies a DIFFERENT protection_order_id
    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
        protection_order_id="AF-S-EXPECTED-LINKED-STOP",
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False
    assert result.manual_escalation_required is True
    pos_rec = next(p for p in result.position_details if p.symbol == "NIFTY")
    assert pos_rec.is_protection_confirmed is False


def test_reconciliation_known_protection_id_cancelled_another_ack_stop_exists() -> None:
    """
    Finding 1 Test 2: Known protection ID points to cancelled stop + another ACK stop
    -> protection remains unconfirmed.
    """
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Linked stop is CANCELLED at broker
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-LINKED",
            client_order_id="AF-S-LINKED",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.CANCELLED,
            created_at=now,
            updated_at=now,
        )
    )
    # Unrelated stop is ACKNOWLEDGED
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-OTHER",
            client_order_id="AF-S-OTHER",
            symbol="NIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
        protection_order_id="AF-S-LINKED",
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False
    pos_rec = next(p for p in result.position_details if p.symbol == "NIFTY")
    assert pos_rec.is_protection_confirmed is False


def test_reconciliation_known_protection_id_wrong_symbol() -> None:
    """Finding 1 Test 3: Known protection ID points to wrong-symbol stop -> unconfirmed."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Linked stop exists on BANKNIFTY instead of NIFTY
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-LINKED",
            client_order_id="AF-S-LINKED",
            symbol="BANKNIFTY",
            side=OrderSide.SELL,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
        protection_order_id="AF-S-LINKED",
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False
    pos_rec = next(p for p in result.position_details if p.symbol == "NIFTY")
    assert pos_rec.is_protection_confirmed is False


def test_reconciliation_known_protection_id_wrong_direction() -> None:
    """Finding 1 Test 4: Known protection ID points to wrong-direction stop -> unconfirmed."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24500.00"),
            status="OPEN",
        )
    )
    # Linked stop exists with BUY side instead of SELL side
    broker.inject_external_order(
        BrokerOrder(
            broker_order_id="BRK-STOP-LINKED",
            client_order_id="AF-S-LINKED",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.STOP,
            order_type=BrokerOrderType.STOP_LOSS,
            status=BrokerOrderStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
    )

    local_pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
        protection_order_id="AF-S-LINKED",
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={"NIFTY": local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False
    pos_rec = next(p for p in result.position_details if p.symbol == "NIFTY")
    assert pos_rec.is_protection_confirmed is False


def test_protection_order_role_and_type_combinations() -> None:
    """Finding 2 Tests 5-9: STOP role and STOP_LOSS type are both strictly mandatory."""
    reconciler = ColdBootReconciler(
        broker=PaperBroker(),
        state_store=InMemoryStateStore(),
        gate=ReconciliationGate(),
    )
    now = datetime.now(UTC)

    # 5. STOP role + MARKET type -> invalid
    ord_stop_market = BrokerOrder(
        broker_order_id="BRK-SM",
        client_order_id="AF-S-SM",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.STOP,
        order_type=BrokerOrderType.MARKET,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    assert not reconciler._is_valid_resting_stop_for_position(
        order=ord_stop_market,
        pos_symbol="NIFTY",
        pos_side=TradeSide.LONG,
        pos_quantity=50,
    )

    # 6. STOP role + LIMIT type -> invalid
    ord_stop_limit = BrokerOrder(
        broker_order_id="BRK-SLIM",
        client_order_id="AF-S-SLIM",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.STOP,
        order_type=BrokerOrderType.LIMIT,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    assert not reconciler._is_valid_resting_stop_for_position(
        order=ord_stop_limit,
        pos_symbol="NIFTY",
        pos_side=TradeSide.LONG,
        pos_quantity=50,
    )

    # 7. ENTRY role + STOP_LOSS type -> invalid
    ord_entry_sl = BrokerOrder(
        broker_order_id="BRK-ESL",
        client_order_id="AF-E-ESL",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.STOP_LOSS,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    assert not reconciler._is_valid_resting_stop_for_position(
        order=ord_entry_sl,
        pos_symbol="NIFTY",
        pos_side=TradeSide.LONG,
        pos_quantity=50,
    )

    # 8. EXIT role + STOP_LOSS type -> invalid
    ord_exit_sl = BrokerOrder(
        broker_order_id="BRK-XSL",
        client_order_id="AF-X-XSL",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.EXIT,
        order_type=BrokerOrderType.STOP_LOSS,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    assert not reconciler._is_valid_resting_stop_for_position(
        order=ord_exit_sl,
        pos_symbol="NIFTY",
        pos_side=TradeSide.LONG,
        pos_quantity=50,
    )

    # 9. STOP role + STOP_LOSS type + exact symbol/side/quantity/active status -> valid
    ord_valid = BrokerOrder(
        broker_order_id="BRK-VALID",
        client_order_id="AF-S-VALID-SL",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.STOP,
        order_type=BrokerOrderType.STOP_LOSS,
        status=BrokerOrderStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    assert reconciler._is_valid_resting_stop_for_position(
        order=ord_valid,
        pos_symbol="NIFTY",
        pos_side=TradeSide.LONG,
        pos_quantity=50,
    )
