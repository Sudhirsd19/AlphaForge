"""
Unit tests for AlphaForge Order State Machine.
Verifies all 16 states, legal transitions, illegal-transition rejection,
terminal-state protection, idempotency, event lineage, and concurrency safety.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import IllegalStateTransitionError, OrderValidationError
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderEvent,
    OrderState,
)
from alphaforge.execution.state_machine import (
    TERMINAL_STATES,
    OrderStateMachine,
)
from alphaforge.risk.enums import TradeSide


def _assert_state(fsm: OrderStateMachine, expected: OrderState) -> None:
    assert fsm.current_state == expected


def test_all_16_states_exist() -> None:
    """Verify all 16 authoritative lifecycle states exist with unique enum values."""
    expected_states = {
        "CREATED",
        "VALIDATED",
        "REJECTED",
        "SUBMITTED",
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "PROTECTION_PENDING",
        "PROTECTED",
        "EXIT_PENDING",
        "PARTIAL_EXIT",
        "CLOSED",
        "CANCELLED",
        "UNKNOWN",
        "RECONCILING",
        "MANUAL_ESCALATION",
    }
    actual_states = {s.value for s in OrderState}
    assert actual_states == expected_states
    assert len(OrderState) == 16


def test_order_state_machine_initialization_valid() -> None:
    """Verify clean initialization of OrderStateMachine."""
    fsm = OrderStateMachine(
        order_id="ORD-001",
        symbol="NIFTY26JUNFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-100",
    )
    assert fsm.order_id == "ORD-001"
    assert fsm.symbol == "NIFTY26JUNFUT"
    assert fsm.side == TradeSide.LONG
    assert fsm.quantity == 50
    assert fsm.current_state == OrderState.CREATED
    assert fsm.filled_quantity == 0
    assert fsm.remaining_quantity == 50
    assert not fsm.is_terminal
    assert not fsm.is_protected
    assert len(fsm.history) == 0


def test_order_state_machine_initialization_invalid_inputs_fail() -> None:
    """Verify non-positive quantity or empty IDs fail closed with OrderValidationError."""
    with pytest.raises(OrderValidationError, match="strictly positive"):
        OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=0,
        )

    with pytest.raises(OrderValidationError, match="non-empty"):
        OrderStateMachine(
            order_id="",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=10,
        )


def test_legal_lifecycle_created_to_protected_to_closed() -> None:
    """Verify complete happy path from CREATED through execution and protection to CLOSED."""
    fsm = OrderStateMachine(
        order_id="ORD-FLOW-1",
        symbol="NIFTY26JUNFUT",
        side=TradeSide.LONG,
        quantity=25,
    )

    # CREATED -> VALIDATED
    res = fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
    assert res.success
    _assert_state(fsm, OrderState.VALIDATED)
    assert len(fsm.history) == 1

    # VALIDATED -> SUBMITTED
    res = fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
    assert res.success
    _assert_state(fsm, OrderState.SUBMITTED)

    # SUBMITTED -> ACKNOWLEDGED
    res = fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
    assert res.success
    _assert_state(fsm, OrderState.ACKNOWLEDGED)

    # ACKNOWLEDGED -> FILLED
    fill_time = datetime.now(UTC)
    res = fsm.transition(
        OrderState.FILLED,
        event=OrderEvent.FULL_FILL,
        timestamp=fill_time,
        fill_price=Decimal("24500.00"),
    )
    assert res.success
    _assert_state(fsm, OrderState.FILLED)
    assert fsm.filled_quantity == 25
    assert fsm.remaining_quantity == 0
    assert fsm.filled_at == fill_time

    # FILLED -> PROTECTION_PENDING
    res = fsm.transition(OrderState.PROTECTION_PENDING, event=OrderEvent.REQUEST_PROTECTION)
    assert res.success
    _assert_state(fsm, OrderState.PROTECTION_PENDING)
    assert not fsm.is_protected

    # PROTECTION_PENDING -> PROTECTED
    res = fsm.transition(
        OrderState.PROTECTED,
        event=OrderEvent.PROTECTION_CONFIRMED,
        protection_order_id="SL-ORD-999",
    )
    assert res.success
    _assert_state(fsm, OrderState.PROTECTED)
    assert fsm.is_protected
    assert fsm.is_protection_confirmed

    # PROTECTED -> EXIT_PENDING
    res = fsm.transition(OrderState.EXIT_PENDING, event=OrderEvent.REQUEST_EXIT)
    assert res.success
    _assert_state(fsm, OrderState.EXIT_PENDING)

    # EXIT_PENDING -> CLOSED
    res = fsm.transition(OrderState.CLOSED, event=OrderEvent.EXIT_FULL_FILL)
    assert res.success
    _assert_state(fsm, OrderState.CLOSED)
    assert fsm.is_terminal
    assert len(fsm.history) == 8


def test_partial_fill_and_partial_exit_flow() -> None:
    """Verify flow involving PARTIALLY_FILLED and PARTIAL_EXIT."""
    fsm = OrderStateMachine(
        order_id="ORD-PARTIAL",
        symbol="NIFTY",
        side=TradeSide.SHORT,
        quantity=100,
    )
    fsm.transition(OrderState.VALIDATED)
    fsm.transition(OrderState.SUBMITTED)
    fsm.transition(OrderState.ACKNOWLEDGED)

    # ACK -> PARTIALLY_FILLED (50 filled)
    fsm.transition(OrderState.PARTIALLY_FILLED, fill_qty=50)
    _assert_state(fsm, OrderState.PARTIALLY_FILLED)
    assert fsm.filled_quantity == 50
    assert fsm.remaining_quantity == 50

    # PARTIALLY_FILLED -> FILLED (remaining filled)
    fsm.transition(OrderState.FILLED, fill_qty=50)
    _assert_state(fsm, OrderState.FILLED)
    assert fsm.filled_quantity == 100
    assert fsm.remaining_quantity == 0

    fsm.transition(OrderState.PROTECTION_PENDING)
    fsm.transition(OrderState.PROTECTED)
    fsm.transition(OrderState.EXIT_PENDING)

    # EXIT_PENDING -> PARTIAL_EXIT
    fsm.transition(OrderState.PARTIAL_EXIT)
    _assert_state(fsm, OrderState.PARTIAL_EXIT)

    # PARTIAL_EXIT -> CLOSED
    fsm.transition(OrderState.CLOSED)
    _assert_state(fsm, OrderState.CLOSED)
    assert fsm.is_terminal


def test_unknown_and_reconciliation_flow() -> None:
    """Verify UNKNOWN -> RECONCILING -> CLOSED."""
    fsm = OrderStateMachine(
        order_id="ORD-RECON",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
    )
    fsm.transition(OrderState.VALIDATED)
    fsm.transition(OrderState.SUBMITTED)

    # Network drops -> UNKNOWN
    fsm.transition(OrderState.UNKNOWN, event=OrderEvent.COMMUNICATION_LOST)
    _assert_state(fsm, OrderState.UNKNOWN)

    # UNKNOWN -> RECONCILING
    fsm.transition(OrderState.RECONCILING, event=OrderEvent.START_RECONCILIATION)
    _assert_state(fsm, OrderState.RECONCILING)

    # RECONCILING -> CLOSED
    fsm.transition(OrderState.CLOSED, reason="Reconciled flat at broker")
    _assert_state(fsm, OrderState.CLOSED)


def test_reconciliation_escalation_flow() -> None:
    """Verify UNKNOWN -> RECONCILING -> MANUAL_ESCALATION."""
    fsm = OrderStateMachine(
        order_id="ORD-ESCALATE",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
    )
    fsm.transition(OrderState.VALIDATED)
    fsm.transition(OrderState.SUBMITTED)
    fsm.transition(OrderState.UNKNOWN)
    fsm.transition(OrderState.RECONCILING)

    # Ambiguity cannot be resolved -> MANUAL_ESCALATION
    fsm.transition(OrderState.MANUAL_ESCALATION, reason="State ambiguity persists at venue")
    assert fsm.current_state == OrderState.MANUAL_ESCALATION
    assert fsm.is_terminal


def test_illegal_transitions_raise_and_do_not_mutate_state() -> None:
    """Verify illegal transitions fail closed with state and history unchanged."""
    fsm = OrderStateMachine(
        order_id="ORD-FAIL",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=25,
    )

    # CREATED cannot jump directly to FILLED, PROTECTED, or CLOSED
    for bad_target in (OrderState.FILLED, OrderState.PROTECTED, OrderState.CLOSED):
        with pytest.raises(IllegalStateTransitionError, match="Illegal transition"):
            fsm.transition(bad_target, raise_on_error=True)
        assert fsm.current_state == OrderState.CREATED
        assert len(fsm.history) == 0

    # With raise_on_error=False, returns structured failure result
    res = fsm.transition(OrderState.FILLED, raise_on_error=False)
    assert not res.success
    assert res.reason_code == ExecutionReasonCode.INVALID_TRANSITION
    assert fsm.current_state == OrderState.CREATED
    assert len(fsm.history) == 0


def test_terminal_states_cannot_resurrect() -> None:
    """Verify REJECTED, CLOSED, CANCELLED, MANUAL_ESCALATION cannot transition forward."""
    for term_state in TERMINAL_STATES:
        fsm = OrderStateMachine(
            order_id=f"ORD-TERM-{term_state.value}",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=10,
            initial_state=term_state,
        )
        assert fsm.is_terminal

        # Attempt transition to normal states
        for target in (
            OrderState.CREATED,
            OrderState.VALIDATED,
            OrderState.SUBMITTED,
            OrderState.FILLED,
        ):
            with pytest.raises(IllegalStateTransitionError, match="terminal state"):
                fsm.transition(target, raise_on_error=True)
            assert fsm.current_state == term_state

            res = fsm.transition(target, raise_on_error=False)
            assert not res.success
            assert res.reason_code == ExecutionReasonCode.TERMINAL_STATE


def test_idempotent_duplicate_transition_noop() -> None:
    """Transitioning to current state produces success without mutating state or history."""
    fsm = OrderStateMachine(
        order_id="ORD-IDEMP",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=10,
    )
    fsm.transition(OrderState.VALIDATED)
    assert len(fsm.history) == 1

    # Duplicate call to VALIDATED
    res = fsm.transition(OrderState.VALIDATED)
    assert res.success
    assert res.reason_code == ExecutionReasonCode.ALREADY_IN_TARGET_STATE
    assert fsm.current_state == OrderState.VALIDATED
    # History length must remain 1 (zero duplicate event added)
    assert len(fsm.history) == 1


def test_order_snapshot_immutability() -> None:
    """Snapshot produces an immutable Order model reflecting current state."""
    fsm = OrderStateMachine(
        order_id="ORD-SNAP",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-9",
    )
    fsm.transition(OrderState.VALIDATED)
    snap = fsm.snapshot()

    assert snap.order_id == "ORD-SNAP"
    assert snap.state == OrderState.VALIDATED
    assert snap.quantity == 50
    assert snap.signal_id == "SIG-9"

    # Mutating snap fails (frozen model)
    with pytest.raises(ValidationError):
        snap.state = OrderState.FILLED


def test_concurrent_transitions_thread_safety() -> None:
    """Verify thread-safety when multiple threads attempt concurrent transitions."""
    fsm = OrderStateMachine(
        order_id="ORD-THREAD",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
    )
    results: list[bool] = []

    def try_submit() -> None:
        try:
            res = fsm.transition(OrderState.VALIDATED, raise_on_error=False)
            results.append(res.success)
        except Exception:
            results.append(False)

    threads = [threading.Thread(target=try_submit) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Order must be in VALIDATED state
    assert fsm.current_state == OrderState.VALIDATED
    # Exactly one transition actually executed; other 9 were deduplicated as NO-OP
    assert len(fsm.history) == 1
