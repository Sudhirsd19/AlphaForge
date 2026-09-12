"""
Property-based tests for AlphaForge Order State Machine & Protection Watchdog using Hypothesis.
Verifies invariants across randomized transition paths:
1. Any invalid transition leaves state and history length strictly unchanged.
2. Terminal states cannot resurrect into active states.
3. LONG emergency exit side is strictly SELL.
4. SHORT emergency exit side is strictly BUY.
5. Watchdog never treats unconfirmed protection as PROTECTED.
6. Exact quantity invariance in emergency commands.
"""

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.core.exceptions import IllegalStateTransitionError
from alphaforge.execution.enums import (
    OrderSide,
    OrderState,
)
from alphaforge.execution.models import Order
from alphaforge.execution.protection_watchdog import ProtectionWatchdog
from alphaforge.execution.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    OrderStateMachine,
)
from alphaforge.risk.enums import TradeSide


@given(
    target_state=st.sampled_from(list(OrderState)),
    quantity=st.integers(min_value=1, max_value=1000),
    side=st.sampled_from([TradeSide.LONG, TradeSide.SHORT]),
)
@settings(max_examples=100)
def test_invalid_transition_preserves_state_and_history_property(
    target_state: OrderState,
    quantity: int,
    side: TradeSide,
) -> None:
    """
    Property: If a transition from current_state to target_state is illegal,
    the current_state and history length are strictly unchanged.
    """
    fsm = OrderStateMachine(
        order_id="ORD-PROP-1",
        symbol="NIFTY",
        side=side,
        quantity=quantity,
    )
    initial_state = fsm.current_state
    allowed = ALLOWED_TRANSITIONS[initial_state]

    if target_state not in allowed and target_state != initial_state:
        # Must fail closed with exception
        try:
            fsm.transition(target_state, raise_on_error=True)
            pytest.fail(
                f"Expected IllegalStateTransitionError for {initial_state} -> {target_state}"
            )
        except IllegalStateTransitionError:
            pass

        assert fsm.current_state == initial_state
        assert len(fsm.history) == 0

        # With raise_on_error=False
        res = fsm.transition(target_state, raise_on_error=False)
        assert not res.success
        assert fsm.current_state == initial_state
        assert len(fsm.history) == 0


@given(
    term_state=st.sampled_from(list(TERMINAL_STATES)),
    target_state=st.sampled_from(list(OrderState)),
)
@settings(max_examples=50)
def test_terminal_states_reject_all_forward_transitions_property(
    term_state: OrderState,
    target_state: OrderState,
) -> None:
    """
    Property: An order in a terminal state cannot transition to any different state.
    Terminal states are permanent and final.
    """
    fsm = OrderStateMachine(
        order_id="ORD-TERM-PROP",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=10,
        initial_state=term_state,
    )
    assert fsm.is_terminal

    if target_state != term_state:
        res = fsm.transition(target_state, raise_on_error=False)
        assert not res.success
        assert fsm.current_state == term_state
        assert len(fsm.history) == 0


@given(
    side=st.sampled_from([TradeSide.LONG, TradeSide.SHORT]),
    quantity=st.integers(min_value=1, max_value=50000),
)
@settings(max_examples=50)
def test_emergency_exit_direction_and_quantity_monotonicity_property(
    side: TradeSide,
    quantity: int,
) -> None:
    """
    Property:
    - For LONG position, emergency exit is ALWAYS SELL.
    - For SHORT position, emergency exit is ALWAYS BUY.
    - Command quantity ALWAYS strictly equals position quantity.
    """
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    order = Order(
        order_id="ORD-PROP-DIR",
        state=OrderState.FILLED,
        symbol="NIFTY",
        side=side,
        quantity=quantity,
        filled_quantity=quantity,
        created_at=now,
        updated_at=now,
    )
    decision = watchdog.evaluate_order(order, current_time=now)

    assert decision.is_emergency
    assert decision.emergency_command is not None
    assert decision.emergency_command.quantity == quantity

    if side == TradeSide.LONG:
        assert decision.emergency_command.side == OrderSide.SELL
    else:
        assert decision.emergency_command.side == OrderSide.BUY


@given(
    state=st.sampled_from(
        [
            OrderState.FILLED,
            OrderState.PROTECTION_PENDING,
            OrderState.UNKNOWN,
            OrderState.RECONCILING,
        ]
    ),
)
@settings(max_examples=50)
def test_unconfirmed_protection_never_treated_as_protected_property(
    state: OrderState,
) -> None:
    """
    Property: Any open position state without explicit confirmation is
    NEVER reported as is_protected=True.
    """
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    order = Order(
        order_id="ORD-PROP-UNCONF",
        state=state,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        is_protection_confirmed=False,
        created_at=now,
        updated_at=now,
    )
    decision = watchdog.evaluate_order(order, current_time=now)
    assert not decision.is_protected
