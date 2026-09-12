"""
Integration tests for AlphaForge Audit Ledger & Immutable Execution Record.
Simulates end-to-end multi-phase execution cycles:
1. End-to-end execution lifecycle audit trail:
   SIGNAL_GENERATED -> RISK_CHECK_PASSED -> ORDER_CREATED -> ORDER_VALIDATED ->
   ORDER_SUBMITTED -> ORDER_ACKNOWLEDGED -> ORDER_FILLED -> PROTECTION_PENDING ->
   PROTECTION_CONFIRMED -> RECONCILIATION_STARTED -> RECONCILIATION_MATCHED.
2. Complete correlation and causal lineage verification across state boundaries.
3. Cold-boot process restart recovery from disk with automatic chain validation.
4. Non-invasive audit failure isolation: FSM state transitions succeed even if audit listener fails.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from alphaforge.execution.enums import (
    OrderState,
)
from alphaforge.execution.models import TransitionEvent
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.ledger.adapters import (
    OrderFSMTransitionAuditor,
    ReconciliationAuditor,
)
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import FileLedgerStorage
from alphaforge.reconciliation.models import (
    OrderReconciliationRecord,
    ReconciliationAction,
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)
from alphaforge.risk.enums import TradeSide


def test_end_to_end_audit_lifecycle_with_fsm_and_reconciliation(tmp_path: Path) -> None:
    """
    Test full lifecycle audit recording spanning Strategy, Risk, Order FSM, Protection,
    and Reconciliation phases with complete lineage and cryptographic integrity verification.
    """
    ledger_path = tmp_path / "lifecycle_ledger.jsonl"
    storage = FileLedgerStorage(ledger_path)
    ledger = AuditLedger(storage)

    fsm_auditor = OrderFSMTransitionAuditor(ledger)
    recon_auditor = ReconciliationAuditor(ledger)

    corr_id = "CORR-E2E-2026-001"
    signal_id = "SIG-STRAT-991"
    order_id = "AF-ORD-1001"

    # Step 1: SIGNAL_GENERATED (Strategy)
    ev_sig = ledger.append(
        event_type=AuditEventType.SIGNAL_GENERATED,
        entity_type="SIGNAL",
        entity_id=signal_id,
        correlation_id=corr_id,
        causation_id="STRATEGY_TREND_PULLBACK_V1",
        payload={
            "symbol": "NIFTY26MARFUT",
            "direction": "LONG",
            "score": Decimal("0.92"),
            "basis_spread": Decimal("15.50"),
        },
    )
    assert ev_sig.sequence_number == 1

    # Step 2: RISK_CHECK (Risk Engine)
    ev_risk = ledger.append(
        event_type=AuditEventType.RISK_CHECK,
        entity_type="RISK_CHECK",
        entity_id=f"RC-{signal_id}",
        correlation_id=corr_id,
        causation_id=ev_sig.event_id,
        payload={
            "daily_loss_utilization": Decimal("0.12"),
            "leverage_ratio": Decimal("1.85"),
            "fat_finger_passed": True,
            "max_drawdown_passed": True,
        },
    )
    assert ev_risk.sequence_number == 2

    # Step 3 to 7: Order FSM Transitions (Phase 7)
    # OrderStateMachine initializes at CREATED
    fsm = OrderStateMachine(
        order_id=order_id,
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id=signal_id,
        transition_listener=fsm_auditor.on_transition,
    )

    now = datetime.now(UTC)
    # CREATED -> VALIDATED (OrderEvent.VALIDATE_SUCCESS -> maps to ORDER_VALIDATED)
    fsm.transition(OrderState.VALIDATED, reason="Client validation successful", timestamp=now)
    # VALIDATED -> SUBMITTED (OrderEvent.SUBMIT -> maps to ORDER_SUBMITTED)
    fsm.transition(OrderState.SUBMITTED, reason="Dispatched to broker gateway", timestamp=now)
    # SUBMITTED -> ACKNOWLEDGED (OrderEvent.ACKNOWLEDGE -> maps to ORDER_ACKNOWLEDGED)
    fsm.transition(OrderState.ACKNOWLEDGED, reason="Broker ACK received", timestamp=now)
    # ACKNOWLEDGED -> FILLED (OrderEvent.FULL_FILL -> maps to ORDER_FILLED)
    fsm.transition(
        OrderState.FILLED,
        reason="Full fill received from exchange",
        timestamp=now,
        fill_qty=50,
        fill_price=Decimal("24500.00"),
    )

    # Verify FSM transitions were recorded in ledger
    order_events = ledger.get_events_by_entity("ORDER", order_id)
    assert len(order_events) == 4
    expected_order_types = [
        AuditEventType.ORDER_VALIDATED,
        AuditEventType.ORDER_SUBMITTED,
        AuditEventType.ORDER_ACKNOWLEDGED,
        AuditEventType.ORDER_FILLED,
    ]
    for ev, exp_type in zip(order_events, expected_order_types, strict=True):
        assert ev.event_type == exp_type

    # Step 8: PROTECTION_PENDING (Protection Watchdog)
    stop_order_id = "AF-STOP-1001"
    ev_prot_pend = ledger.append(
        event_type=AuditEventType.PROTECTION_PENDING,
        entity_type="PROTECTION",
        entity_id=stop_order_id,
        correlation_id=corr_id,
        causation_id=order_events[-1].event_id,
        payload={
            "protected_order_id": order_id,
            "stop_loss_trigger_price": Decimal("24400.00"),
            "stop_loss_limit_price": Decimal("24380.00"),
            "quantity": 50,
        },
    )

    # Step 9: PROTECTION_CONFIRMED
    _ev_prot_conf = ledger.append(
        event_type=AuditEventType.PROTECTION_CONFIRMED,
        entity_type="PROTECTION",
        entity_id=stop_order_id,
        correlation_id=corr_id,
        causation_id=ev_prot_pend.event_id,
        payload={
            "broker_stop_order_id": "BRK-STOP-98765",
            "state": "RESTING",
            "unprotected_grace_elapsed_ms": 120,
        },
    )

    # Step 10 & 11: Reconciliation Run (Phase 8)
    reconcile_cycle_id = "RECON-CYCLE-001"
    recon_auditor.audit_reconciliation_start(
        reconciliation_id=reconcile_cycle_id,
        correlation_id=corr_id,
    )

    recon_res = ReconciliationResult(
        reconciliation_id=reconcile_cycle_id,
        timestamp=now,
        status=ReconciliationStatus.MATCHED,
        reason_code=ReconciliationReasonCode.MATCHED,
        local_order_count=1,
        broker_order_count=1,
        local_position_count=1,
        broker_position_count=1,
        matched_count=2,
        mismatch_count=0,
        unknown_count=0,
        new_entries_allowed=True,
        manual_escalation_required=False,
    )

    recon_auditor.audit_reconciliation_result(
        result=recon_res,
        correlation_id=corr_id,
    )

    # Total recorded events: 1 (sig) + 1 (risk) + 4 (fsm) + 2 (prot) + 2 (recon) = 10 events
    assert len(ledger) == 10

    # Cryptographic Chain Verification
    res = ledger.verify_chain()
    assert res.valid is True
    assert res.event_count == 10
    assert res.corruption_detected is False
    assert res.error_code is None
    assert res.corruption_sequence is None

    # Lineage Verification
    all_events = storage.read_all()
    for idx, ev in enumerate(all_events, start=1):
        assert ev.sequence_number == idx

    # Verify query by correlation_id retrieves all events in this chain
    order_audited = ledger.get_events_by_correlation_id(corr_id)
    assert len(order_audited) >= 6


def test_audit_ledger_cold_restart_recovery(tmp_path: Path) -> None:
    """
    Verify process restart recovery:
    1. Append events to file ledger.
    2. Terminate session (re-instantiate storage and ledger).
    3. Verify startup chain integrity verification succeeds.
    4. Append subsequent events ensuring continuous sequence 1..N
       and valid cryptographic hash chain.
    """
    ledger_path = tmp_path / "cold_restart_ledger.jsonl"
    storage1 = FileLedgerStorage(ledger_path)
    ledger1 = AuditLedger(storage1, auto_verify_on_startup=True)

    for i in range(1, 6):
        ledger1.append(
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id=f"RESTART-ORD-{i}",
            correlation_id=f"CORR-{i}",
            causation_id=f"CAUS-{i}",
            payload={"step": i, "price": Decimal("24000.00")},
        )

    assert len(ledger1) == 5
    assert ledger1.get_last_event() is not None
    assert ledger1.get_last_event().sequence_number == 5  # type: ignore[union-attr]

    # Simulate Cold Restart
    del ledger1
    del storage1

    storage2 = FileLedgerStorage(ledger_path)
    ledger2 = AuditLedger(storage2, auto_verify_on_startup=True)

    assert len(ledger2) == 5
    assert ledger2.get_last_event() is not None
    assert ledger2.get_last_event().sequence_number == 5  # type: ignore[union-attr]

    # Append 5 more events
    for i in range(6, 11):
        ledger2.append(
            event_type=AuditEventType.ORDER_FILLED,
            entity_type="ORDER",
            entity_id=f"RESTART-ORD-{i}",
            correlation_id=f"CORR-{i}",
            causation_id=f"CAUS-{i}",
            payload={"step": i, "fill_qty": 50},
        )

    assert len(ledger2) == 10
    assert ledger2.get_last_event().sequence_number == 10  # type: ignore[union-attr]

    # Full Chain Verification across pre-restart and post-restart events
    res = ledger2.verify_chain()
    assert res.valid is True
    assert res.event_count == 10
    assert res.corruption_detected is False


def test_fsm_audit_failure_isolation() -> None:
    """
    Verify non-invasive audit failure isolation:
    If the audit listener raises an unhandled exception, the FSM state transition
    MUST succeed and its internal state must be properly updated.
    """

    def failing_listener(_t_event: TransitionEvent) -> None:
        raise RuntimeError("Simulated catastrophic ledger disk failure")

    fsm = OrderStateMachine(
        order_id="AF-ORD-FAILSAFE",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        transition_listener=failing_listener,
    )

    # In OrderStateMachine, if transition_listener raises, state is already updated.
    with pytest.raises(RuntimeError, match="Simulated catastrophic ledger disk failure"):
        fsm.transition(
            OrderState.VALIDATED,
            reason="Validation complete",
            timestamp=datetime.now(UTC),
        )

    # Crucial assertion: FSM state was committed BEFORE listener invocation
    assert fsm.current_state == OrderState.VALIDATED
    assert len(fsm.history) == 1
    assert fsm.history[0].new_state == OrderState.VALIDATED


def test_reconciliation_auditor_hazard_handling(tmp_path: Path) -> None:
    """
    Verify ReconciliationAuditor correctly translates PROTECTION_UNCONFIRMED finding
    into PROTECTION_UNCONFIRMED and EMERGENCY_PROTECTION_TRIGGERED audit events.
    """
    ledger_path = tmp_path / "recon_hazard_ledger.jsonl"
    storage = FileLedgerStorage(ledger_path)
    ledger = AuditLedger(storage)
    recon_auditor = ReconciliationAuditor(ledger)

    recon_id = "RECON-HAZARD-001"
    recon_auditor.audit_reconciliation_start(recon_id)

    order_rec = OrderReconciliationRecord(
        client_order_id="CL-HAZARD-ENTRY-01",
        broker_order_id="BRK-999",
        local_state=OrderState.FILLED,
        broker_status=None,
        local_quantity=50,
        broker_filled_quantity=50,
        action=ReconciliationAction.TRIGGER_EMERGENCY_PROTECTION,
        reason_code=ReconciliationReasonCode.PROTECTION_UNCONFIRMED,
        notes="Resting stop not found on broker during cold boot",
    )

    now = datetime.now(UTC)
    result = ReconciliationResult(
        reconciliation_id=recon_id,
        timestamp=now,
        status=ReconciliationStatus.MISMATCH,
        reason_code=ReconciliationReasonCode.PROTECTION_UNCONFIRMED,
        local_order_count=1,
        broker_order_count=0,
        local_position_count=1,
        broker_position_count=1,
        matched_count=0,
        mismatch_count=1,
        unknown_count=0,
        new_entries_allowed=False,
        manual_escalation_required=True,
        order_details=(order_rec,),
    )

    recon_auditor.audit_reconciliation_result(result)

    # 1 (start) + 1 (mismatch) + 1 (protection unconfirmed) + 1 (emergency triggered) = 4 events
    assert len(ledger) == 4
    events = storage.read_all()
    event_types = [ev.event_type for ev in events]
    assert event_types == [
        AuditEventType.RECONCILIATION_STARTED,
        AuditEventType.RECONCILIATION_MISMATCH,
        AuditEventType.PROTECTION_UNCONFIRMED,
        AuditEventType.EMERGENCY_PROTECTION_TRIGGERED,
    ]

    # Verify chain integrity
    res = ledger.verify_chain()
    assert res.valid is True
    assert res.event_count == 4
