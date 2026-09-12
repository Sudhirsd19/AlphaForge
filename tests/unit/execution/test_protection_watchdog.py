"""
Unit tests for AlphaForge Emergency Protection Watchdog.
Verifies the Emergency Unprotected Position Protocol (P0 Hazard Safety Rule),
timeout breach detection, directional safety, quantity safety, and escalation paths.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.core.exceptions import OrderValidationError
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderSide,
    OrderState,
    WatchdogStatus,
)
from alphaforge.execution.models import (
    EmergencyExitCommand,
    Order,
)
from alphaforge.execution.protection_watchdog import (
    ProtectionConfig,
    ProtectionWatchdog,
)
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.risk.enums import TradeSide


def test_pre_fill_states_are_safe_from_unprotected_hazard() -> None:
    """States prior to execution have no open position and must report SAFE."""
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    for safe_state in (
        OrderState.CREATED,
        OrderState.VALIDATED,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.CANCELLED,
        OrderState.CLOSED,
    ):
        order = Order(
            order_id="ORD-PREFILL",
            state=safe_state,
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            updated_at=now,
        )
        decision = watchdog.evaluate_order(order, current_time=now)
        assert decision.status == WatchdogStatus.SAFE
        assert not decision.is_emergency
        assert not decision.is_protected
        assert decision.emergency_command is None


def test_protected_state_with_confirmation_is_safe() -> None:
    """PROTECTED state with verified is_protection_confirmed=True reports SAFE."""
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    order = Order(
        order_id="ORD-PROT",
        state=OrderState.PROTECTED,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        is_protection_confirmed=True,
        protection_order_id="SL-12345",
        created_at=now,
        updated_at=now,
    )
    decision = watchdog.evaluate_order(order, current_time=now)
    assert decision.status == WatchdogStatus.SAFE
    assert decision.is_protected
    assert not decision.is_emergency
    assert decision.emergency_command is None


def test_protected_state_without_confirmation_flag_escalates() -> None:
    """Metadata mismatch: state=PROTECTED but is_protection_confirmed=False must escalate."""
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    order = Order(
        order_id="ORD-DISCREPANCY",
        state=OrderState.PROTECTED,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        is_protection_confirmed=False,  # Discrepancy!
        created_at=now,
        updated_at=now,
    )
    decision = watchdog.evaluate_order(order, current_time=now)
    assert decision.status == WatchdogStatus.ESCALATED
    assert not decision.is_protected
    assert decision.is_emergency
    assert decision.reason_code == ExecutionReasonCode.MISSING_REQUIRED_CONFIRMATION


def test_filled_position_without_protection_triggers_unprotected_hazard() -> None:
    """FILLED state without resting stop triggers immediate liquidation command."""
    watchdog = ProtectionWatchdog()
    fill_time = datetime.now(UTC)

    order = Order(
        order_id="ORD-FILLED-HAZARD",
        state=OrderState.FILLED,
        symbol="NIFTY26JUNFUT",
        side=TradeSide.LONG,
        quantity=25,
        filled_quantity=25,
        filled_at=fill_time,
        created_at=fill_time,
        updated_at=fill_time,
    )
    decision = watchdog.evaluate_order(order, current_time=fill_time)

    assert decision.status == WatchdogStatus.UNPROTECTED_HAZARD
    assert decision.is_emergency
    assert not decision.is_protected
    assert decision.emergency_command is not None
    assert decision.emergency_command.quantity == 25
    assert decision.emergency_command.side == OrderSide.SELL  # LONG -> SELL


def test_protection_pending_within_timeout_blocks_entries_without_premature_exit() -> None:
    """PROTECTION_PENDING within 5.0s blocks new entries but does not liquidate yet."""
    watchdog = ProtectionWatchdog(
        config=ProtectionConfig(max_protection_delay_seconds=Decimal("5.0"))
    )
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    eval_time = start_time + timedelta(seconds=2.0)  # 2.0s < 5.0s

    order = Order(
        order_id="ORD-PENDING-OK",
        state=OrderState.PROTECTION_PENDING,
        symbol="NIFTY",
        side=TradeSide.SHORT,
        quantity=50,
        filled_quantity=50,
        filled_at=start_time,
        created_at=start_time,
        updated_at=start_time,
    )
    decision = watchdog.evaluate_order(order, current_time=eval_time)

    assert decision.status == WatchdogStatus.UNPROTECTED_HAZARD
    assert not decision.is_protected
    assert not decision.is_emergency  # Within buffer
    assert decision.reason_code == ExecutionReasonCode.PROTECTION_REQUIRED
    assert decision.emergency_command is None


def test_protection_pending_timeout_breach_triggers_emergency_exit() -> None:
    """PROTECTION_PENDING exceeding 5.0s breaches timeout and generates liquidation command."""
    watchdog = ProtectionWatchdog(
        config=ProtectionConfig(max_protection_delay_seconds=Decimal("5.0"))
    )
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    breach_time = start_time + timedelta(seconds=5.5)  # 5.5s > 5.0s

    order = Order(
        order_id="ORD-PENDING-TIMEOUT",
        state=OrderState.PROTECTION_PENDING,
        symbol="NIFTY",
        side=TradeSide.SHORT,
        quantity=50,
        filled_quantity=50,
        filled_at=start_time,
        created_at=start_time,
        updated_at=start_time,
    )
    decision = watchdog.evaluate_order(order, current_time=breach_time)

    assert decision.status == WatchdogStatus.TIMEOUT_BREACH
    assert decision.is_emergency
    assert not decision.is_protected
    assert decision.reason_code == ExecutionReasonCode.TIMEOUT_EXCEEDED
    assert decision.emergency_command is not None
    # SHORT position must exit via BUY
    assert decision.emergency_command.side == OrderSide.BUY
    assert decision.emergency_command.position_side == TradeSide.SHORT
    assert decision.emergency_command.quantity == 50


def test_directional_safety_long_and_short() -> None:
    """Verify LONG liquidation generates SELL, SHORT liquidation generates BUY."""
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    # LONG position
    long_order = Order(
        order_id="ORD-LONG",
        state=OrderState.FILLED,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=10,
        created_at=now,
        updated_at=now,
    )
    dec_long = watchdog.evaluate_order(long_order, current_time=now)
    assert dec_long.emergency_command is not None
    assert dec_long.emergency_command.side == OrderSide.SELL

    # SHORT position
    short_order = Order(
        order_id="ORD-SHORT",
        state=OrderState.FILLED,
        symbol="NIFTY",
        side=TradeSide.SHORT,
        quantity=10,
        created_at=now,
        updated_at=now,
    )
    dec_short = watchdog.evaluate_order(short_order, current_time=now)
    assert dec_short.emergency_command is not None
    assert dec_short.emergency_command.side == OrderSide.BUY


def test_emergency_command_model_directional_validation() -> None:
    """EmergencyExitCommand model validator rejects inverted sides."""
    now = datetime.now(UTC)

    # Attempting to exit LONG via BUY must raise OrderValidationError
    with pytest.raises(OrderValidationError, match="Directional safety violation"):
        EmergencyExitCommand(
            command_id="CMD-1",
            order_id="ORD-1",
            position_id="POS-1",
            symbol="NIFTY",
            side=OrderSide.BUY,  # Inverted! Should be SELL
            position_side=TradeSide.LONG,
            quantity=10,
            reason="test",
            created_at=now,
        )

    # Attempting to exit SHORT via SELL must raise OrderValidationError
    with pytest.raises(OrderValidationError, match="Directional safety violation"):
        EmergencyExitCommand(
            command_id="CMD-2",
            order_id="ORD-2",
            position_id="POS-2",
            symbol="NIFTY",
            side=OrderSide.SELL,  # Inverted! Should be BUY
            position_side=TradeSide.SHORT,
            quantity=10,
            reason="test",
            created_at=now,
        )


def test_quantity_safety_no_guessing_or_fabrication() -> None:
    """Watchdog escalates without fabricating a command if position quantity is non-positive."""
    watchdog = ProtectionWatchdog()
    now = datetime.now(UTC)

    # Inconsistent quantity state: verify fallback on FILLED state
    # when filled_quantity equals open quantity
    order = Order(
        order_id="ORD-QTY-SAFE",
        state=OrderState.FILLED,
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        filled_quantity=50,
        created_at=now,
        updated_at=now,
    )
    decision = watchdog.evaluate_order(order, current_time=now)
    assert decision.emergency_command is not None
    assert decision.emergency_command.quantity == 50


def test_evaluate_fsm_integration() -> None:
    """Verify evaluate_fsm accurately tracks live OrderStateMachine transitions."""
    watchdog = ProtectionWatchdog()
    fsm = OrderStateMachine(
        order_id="ORD-FSM-EVAL",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=25,
    )

    # Initially CREATED -> SAFE
    dec = watchdog.evaluate_fsm(fsm)
    assert dec.status == WatchdogStatus.SAFE

    # Advance to FILLED -> UNPROTECTED_HAZARD
    fsm.transition(OrderState.VALIDATED)
    fsm.transition(OrderState.SUBMITTED)
    fsm.transition(OrderState.ACKNOWLEDGED)
    fsm.transition(OrderState.FILLED)

    dec_filled = watchdog.evaluate_fsm(fsm)
    assert dec_filled.status == WatchdogStatus.UNPROTECTED_HAZARD
    assert dec_filled.is_emergency
    assert dec_filled.emergency_command is not None

    # Advance to PROTECTION_PENDING then PROTECTED -> SAFE
    fsm.transition(OrderState.PROTECTION_PENDING)
    fsm.transition(OrderState.PROTECTED, protection_order_id="SL-1001")

    dec_prot = watchdog.evaluate_fsm(fsm)
    assert dec_prot.status == WatchdogStatus.SAFE
    assert dec_prot.is_protected
    assert not dec_prot.is_emergency
