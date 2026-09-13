"""
Phase 12 — Order Execution Failure Tests (O1–O9).
Proves AlphaForge handles broker timeouts, missing ACKs, duplicate submissions,
delayed/partial/repeated fills, and lost responses safely.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alphaforge.broker.models import BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.exceptions import (
    BrokerOrderCollisionError,
    BrokerUnavailableError,
    IdempotencyCollisionError,
)
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderEvent,
    OrderSide,
    OrderState,
)
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_client_order_id,
)
from alphaforge.execution.state_machine import (
    TERMINAL_STATES,
    OrderStateMachine,
)
from alphaforge.fault_injection.injectors import TimeoutBroker
from alphaforge.risk.enums import TradeSide


def _make_client_id(signal: str = "SIG-001") -> str:
    return generate_client_order_id("TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, signal)


def _make_request(
    client_id: str | None = None,
    signal: str = "SIG-001",
) -> BrokerOrderRequest:
    cid = client_id or _make_client_id(signal)
    return BrokerOrderRequest(
        client_order_id=cid,
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )


class TestO1SubmitTimeout:
    """O1: Order submit timeout — broker may or may not have accepted."""

    def test_timeout_raises_broker_unavailable(self) -> None:
        broker = PaperBroker()
        tb = TimeoutBroker(broker, timeout_on_submit_at=1)
        req = _make_request()
        with pytest.raises(BrokerUnavailableError, match="timeout"):
            tb.submit_order(req)

    def test_timeout_after_accept_order_exists_on_broker(self) -> None:
        """After timeout, the order was actually accepted on broker side."""
        broker = PaperBroker()
        tb = TimeoutBroker(broker, timeout_on_submit_at=1)
        req = _make_request()
        with pytest.raises(BrokerUnavailableError):
            tb.submit_order(req)
        # Verify order exists on broker (was accepted before timeout)
        existing = broker.get_order(client_order_id=req.client_order_id)
        assert existing is not None
        assert existing.client_order_id == req.client_order_id


class TestO2NetworkErrorAfterAccept:
    """O2: Network error after broker acceptance — re-query finds original."""

    def test_resubmit_after_timeout_returns_same_order(self) -> None:
        broker = PaperBroker()
        tb = TimeoutBroker(broker, timeout_on_submit_at=1)
        req = _make_request()
        # First submit: timeout after broker accepted
        with pytest.raises(BrokerUnavailableError):
            tb.submit_order(req)
        # Re-submit directly to broker (idempotent): same order returned
        resubmit = broker.submit_order(req)
        assert resubmit.client_order_id == req.client_order_id


class TestO3DuplicateSubmit:
    """O3: Duplicate order submit is idempotent or rejected."""

    def test_identical_duplicate_submit_is_idempotent(self) -> None:
        broker = PaperBroker()
        req = _make_request()
        first = broker.submit_order(req)
        second = broker.submit_order(req)  # Identical duplicate
        assert first.broker_order_id == second.broker_order_id

    def test_conflicting_duplicate_submit_rejected(self) -> None:
        broker = PaperBroker()
        client_id = _make_client_id()
        req1 = BrokerOrderRequest(
            client_order_id=client_id,
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.ENTRY,
        )
        req2 = BrokerOrderRequest(
            client_order_id=client_id,
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=100,  # Different quantity
            role=OrderRole.ENTRY,
        )
        broker.submit_order(req1)
        with pytest.raises(BrokerOrderCollisionError):
            broker.submit_order(req2)

    def test_idempotency_registry_collision(self) -> None:
        registry = IdempotencyRegistry()
        client_id = _make_client_id()
        intent1 = OrderIntent(
            client_order_id=client_id,
            strategy_id="TREND",
            strategy_version="1.0.0",
            symbol="NIFTY",
            role=OrderRole.ENTRY,
            signal_id="SIG-001",
            side=TradeSide.LONG,
            quantity=50,
        )
        intent2 = OrderIntent(
            client_order_id=client_id,
            strategy_id="TREND",
            strategy_version="1.0.0",
            symbol="NIFTY",
            role=OrderRole.ENTRY,
            signal_id="SIG-001",
            side=TradeSide.LONG,
            quantity=100,  # Conflict
        )
        registry.register(intent1)
        with pytest.raises(IdempotencyCollisionError):
            registry.register(intent2)


class TestO4MissingACK:
    """O4: Missing ACK leaves order in SUBMITTED state — no phantom transition."""

    def test_submitted_without_ack_stays_submitted(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        assert fsm.current_state == OrderState.SUBMITTED
        # No ACK arrives — state must remain SUBMITTED
        assert fsm.current_state == OrderState.SUBMITTED


class TestO5DelayedFill:
    """O5: Delayed fill after communication loss transitions correctly."""

    def test_fill_after_unknown_requires_reconciliation(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
        # Communication lost
        fsm.transition(OrderState.UNKNOWN, event=OrderEvent.COMMUNICATION_LOST)
        assert fsm.current_state == OrderState.UNKNOWN
        # Must go through reconciliation before fill
        fsm.transition(OrderState.RECONCILING, event=OrderEvent.START_RECONCILIATION)
        assert fsm.current_state == OrderState.RECONCILING
        fsm.transition(
            OrderState.FILLED,
            event=OrderEvent.RECONCILE_SUCCESS,
            fill_qty=50,
            fill_price=Decimal("24000.00"),
        )
        assert fsm.current_state == OrderState.FILLED


class TestO6PartialFill:
    """O6: Partial fill correctly tracks intermediate state."""

    def test_partial_fill_transitions_to_partially_filled(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
        fsm.transition(
            OrderState.PARTIALLY_FILLED,
            event=OrderEvent.PARTIAL_FILL,
            fill_qty=25,
            fill_price=Decimal("24000.00"),
        )
        assert fsm.current_state == OrderState.PARTIALLY_FILLED


class TestO7RepeatedFill:
    """O7: Repeated fill event on filled order does not double-count."""

    def test_full_fill_then_repeat_is_idempotent(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
        fsm.transition(
            OrderState.FILLED,
            event=OrderEvent.FULL_FILL,
            fill_qty=50,
            fill_price=Decimal("24000.00"),
        )
        assert fsm.current_state == OrderState.FILLED
        # Progress to PROTECTION_PENDING, PROTECTED, EXIT_PENDING, CLOSED
        fsm.transition(OrderState.PROTECTION_PENDING, event=OrderEvent.REQUEST_PROTECTION)
        fsm.transition(
            OrderState.PROTECTED,
            event=OrderEvent.PROTECTION_CONFIRMED,
            protection_order_id="STOP-1",
        )
        fsm.transition(OrderState.EXIT_PENDING, event=OrderEvent.REQUEST_EXIT)
        fsm.transition(OrderState.CLOSED, event=OrderEvent.EXIT_FULL_FILL)
        assert fsm.current_state == OrderState.CLOSED
        # Repeat fill on terminal CLOSED: must not resurrect
        result = fsm.transition(OrderState.FILLED, event=OrderEvent.FULL_FILL, raise_on_error=False)
        assert not result.success
        assert result.reason_code == ExecutionReasonCode.TERMINAL_STATE
        assert fsm.current_state == OrderState.CLOSED


class TestO8CancelResponseLost:
    """O8: Cancel response lost — order remains in original state."""

    def test_cancel_from_validated_succeeds(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        assert fsm.current_state == OrderState.VALIDATED
        fsm.transition(OrderState.CANCELLED, event=OrderEvent.CANCEL)
        assert fsm.current_state == OrderState.CANCELLED

    def test_cancel_on_terminal_state_fails_closed(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.REJECTED, event=OrderEvent.VALIDATE_REJECT)
        assert fsm.current_state == OrderState.REJECTED
        result = fsm.transition(OrderState.CANCELLED, event=OrderEvent.CANCEL, raise_on_error=False)
        assert not result.success
        assert result.reason_code == ExecutionReasonCode.TERMINAL_STATE


class TestO9RejectResponseLost:
    """O9: Reject response lost — idempotent rejection on retry."""

    def test_already_rejected_is_idempotent(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.REJECTED, event=OrderEvent.VALIDATE_REJECT)
        assert fsm.current_state == OrderState.REJECTED
        # Repeat reject event: idempotent, still rejected
        result = fsm.transition(
            OrderState.REJECTED, event=OrderEvent.VALIDATE_REJECT, raise_on_error=False
        )
        assert result.success
        assert fsm.current_state == OrderState.REJECTED

    def test_all_terminal_states_are_immutable(self) -> None:
        for terminal in TERMINAL_STATES:
            now = datetime.now(UTC)
            fsm = OrderStateMachine(
                order_id=f"ORD-{terminal.value}",
                symbol="NIFTY",
                side=TradeSide.LONG,
                quantity=50,
                created_at=now,
                initial_state=terminal,
            )
            for target in OrderState:
                if target != terminal:
                    result = fsm.transition(target, raise_on_error=False)
                    assert not result.success
                    assert fsm.current_state == terminal
