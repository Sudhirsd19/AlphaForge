"""
Phase 12 — Risk-State Failure Tests.
Proves risk limits are enforced under adversarial conditions:
max positions, portfolio risk, daily loss, circuit breaker, reserved risk,
and restart with active reservations.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import RiskValidationError
from alphaforge.risk.engine import (
    evaluate_trade_risk,
    reconcile_and_deduplicate_reservations,
)
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import (
    PortfolioRiskState,
    RiskConfig,
    RiskInput,
    RiskReservation,
)


def _make_input(
    signal_id: str = "SIG-R001",
    equity: Decimal = Decimal("1000000"),
    entry: Decimal = Decimal("2000"),
    stop: Decimal = Decimal("1990"),
    side: TradeSide = TradeSide.LONG,
    quantity: int | None = 50,
) -> RiskInput:
    return RiskInput(
        signal_id=signal_id,
        symbol="NIFTY",
        side=side,
        entry_price=entry,
        stop_price=stop,
        contract_id="NIFTY-2026-09",
        lot_size=50,
        contract_multiplier=Decimal("1"),
        account_equity=equity,
        available_capital=equity,
        proposed_quantity=quantity,
        evaluation_timestamp=datetime.now(UTC),
    )


def _make_portfolio(
    equity: Decimal = Decimal("1000000"),
    open_trades: int = 0,
    reserved_risk: Decimal = Decimal("0"),
    reserved_notional: Decimal = Decimal("0"),
    reservations: tuple[RiskReservation, ...] = (),
) -> PortfolioRiskState:
    return PortfolioRiskState(
        account_equity=equity,
        available_capital=equity,
        open_trade_count=open_trades,
        reserved_risk=reserved_risk,
        reserved_notional=reserved_notional,
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=reservations,
    )


def _make_reservation(
    res_id: str = "RES-001",
    signal_id: str = "SIG-EXISTING",
    risk: Decimal = Decimal("500"),
    notional: Decimal = Decimal("100000"),
) -> RiskReservation:
    return RiskReservation(
        reservation_id=res_id,
        signal_id=signal_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("2000"),
        stop_price=Decimal("1990"),
        quantity=50,
        monetary_risk=risk,
        notional=notional,
        created_timestamp=datetime.now(UTC),
    )


class TestMaxOpenPositions:
    """Risk: max open trades limit enforced."""

    def test_max_open_trades_exceeded_rejected(self) -> None:
        config = RiskConfig(max_open_trades=2)
        reservations = tuple(_make_reservation(f"RES-{i}", f"SIG-{i}") for i in range(2))
        portfolio = _make_portfolio(
            open_trades=2,
            reserved_risk=Decimal("1000"),
            reserved_notional=Decimal("200000"),
            reservations=reservations,
        )
        trade = _make_input(signal_id="SIG-NEW")
        decision = evaluate_trade_risk(trade, portfolio, config)
        assert decision.decision == RiskDecisionState.REJECTED
        assert decision.reason_code == RiskReasonCode.MAX_OPEN_TRADES_EXCEEDED


class TestPortfolioRiskExceeded:
    """Risk: portfolio risk limit enforced."""

    def test_portfolio_risk_exceeded_rejected(self) -> None:
        config = RiskConfig(max_portfolio_risk=Decimal("0.0050"))
        # Already at max risk
        res = _make_reservation(
            "RES-1",
            "SIG-1",
            risk=Decimal("4900"),
            notional=Decimal("900000"),
        )
        portfolio = _make_portfolio(
            reserved_risk=Decimal("4900"),
            reserved_notional=Decimal("900000"),
            reservations=(res,),
            open_trades=1,
        )
        trade = _make_input(signal_id="SIG-NEW-PR")
        decision = evaluate_trade_risk(trade, portfolio, config)
        assert decision.decision == RiskDecisionState.REJECTED


class TestDailyLossLimit:
    """Risk: daily loss hard limit triggers circuit breaker."""

    def test_daily_loss_hard_limit_rejects_new_trade(self) -> None:
        config = RiskConfig(
            daily_loss_soft_limit=Decimal("0.0100"),
            daily_loss_hard_limit=Decimal("0.0200"),
        )
        equity = Decimal("1000000")
        # Current equity shows 2.5% daily loss
        current = equity - Decimal("25000")
        portfolio = PortfolioRiskState(
            account_equity=equity,
            available_capital=equity,
            open_trade_count=0,
            reserved_risk=Decimal("0"),
            reserved_notional=Decimal("0"),
            daily_starting_equity=equity,
            current_equity=current,
            active_reservations=(),
        )
        trade = _make_input(signal_id="SIG-LOSS")
        decision = evaluate_trade_risk(trade, portfolio, config)
        assert decision.decision == RiskDecisionState.REJECTED
        assert decision.reason_code == RiskReasonCode.DAILY_LOSS_LIMIT_REACHED


class TestDuplicateRiskReservation:
    """Risk: duplicate reservation for same signal rejected."""

    def test_duplicate_signal_reservation_rejected(self) -> None:
        res = _make_reservation("RES-DUP", "SIG-DUP")
        portfolio = _make_portfolio(
            reserved_risk=Decimal("500"),
            reserved_notional=Decimal("100000"),
            reservations=(res,),
            open_trades=1,
        )
        trade = _make_input(signal_id="SIG-DUP")
        decision = evaluate_trade_risk(trade, portfolio)
        assert decision.decision == RiskDecisionState.REJECTED
        assert decision.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION


class TestReservationConflict:
    """Risk: conflicting reservation data fails closed."""

    def test_conflicting_reservation_ids_rejected(self) -> None:
        res1 = _make_reservation("RES-CONF", "SIG-CONF1", risk=Decimal("500"))
        res2 = RiskReservation(
            reservation_id="RES-CONF",  # Same ID
            signal_id="SIG-CONF2",  # Different signal
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_price=Decimal("24000"),
            stop_price=Decimal("23900"),
            quantity=50,
            monetary_risk=Decimal("600"),  # Different risk
            notional=Decimal("100000"),
            created_timestamp=datetime.now(UTC),
        )
        with pytest.raises(RiskValidationError, match="Conflicting"):
            reconcile_and_deduplicate_reservations((res1, res2))


class TestRestartWithActiveReservation:
    """Risk: restart with persisted reservations enforces limits."""

    def test_persisted_reservation_blocks_duplicate(self) -> None:
        # After restart, portfolio state reconstructed from persistence
        res = _make_reservation("RES-RESTART", "SIG-RESTART")
        portfolio = _make_portfolio(
            reserved_risk=Decimal("500"),
            reserved_notional=Decimal("100000"),
            reservations=(res,),
            open_trades=1,
        )
        # Same signal tries again
        trade = _make_input(signal_id="SIG-RESTART")
        decision = evaluate_trade_risk(trade, portfolio)
        assert decision.decision == RiskDecisionState.REJECTED
        assert decision.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION


class TestReservationDeduplication:
    """Risk: identical reservations are deduplicated, not double-counted."""

    def test_identical_reservations_deduplicated(self) -> None:
        res = _make_reservation("RES-DEDUP", "SIG-DEDUP")
        # Pass same reservation twice
        result = reconcile_and_deduplicate_reservations((res, res))
        assert len(result) == 1
        assert result[0].reservation_id == "RES-DEDUP"


class TestInvalidReservationData:
    """Risk: invalid reservation data fails closed."""

    def test_zero_monetary_risk_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskReservation(
                reservation_id="RES-BAD",
                signal_id="SIG-BAD",
                symbol="NIFTY",
                side=TradeSide.LONG,
                entry_price=Decimal("24000"),
                stop_price=Decimal("23900"),
                quantity=50,
                monetary_risk=Decimal("0"),  # Invalid
                notional=Decimal("100000"),
                created_timestamp=datetime.now(UTC),
            )

    def test_negative_quantity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskReservation(
                reservation_id="RES-NEG",
                signal_id="SIG-NEG",
                symbol="NIFTY",
                side=TradeSide.LONG,
                entry_price=Decimal("24000"),
                stop_price=Decimal("23900"),
                quantity=-10,  # Invalid
                monetary_risk=Decimal("500"),
                notional=Decimal("100000"),
                created_timestamp=datetime.now(UTC),
            )
