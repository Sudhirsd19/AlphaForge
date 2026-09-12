"""
Unit tests for alphaforge.backtest.models.
Verifies immutability, validation bounds, UTC timezone enforcement, and schema constraints.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.backtest.models import (
    BacktestConfig,
    BacktestTrade,
    EquitySnapshot,
    FinalPositionPolicy,
    QuantGateStatus,
    RegimeType,
    ValidationStatus,
)
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.risk.enums import TradeSide


def test_enums_values() -> None:
    """Verify enum taxonomy string values."""
    assert FinalPositionPolicy.MARK_TO_MARKET == "MARK_TO_MARKET"
    assert FinalPositionPolicy.FORCE_CLOSE == "FORCE_CLOSE"

    assert ValidationStatus.VALID == "VALID"
    assert ValidationStatus.WARNING == "WARNING"
    assert ValidationStatus.INVALID == "INVALID"
    assert ValidationStatus.INSUFFICIENT_DATA == "INSUFFICIENT_DATA"

    assert RegimeType.TRENDING_BULL == "TRENDING_BULL"
    assert RegimeType.TRENDING_BEAR == "TRENDING_BEAR"
    assert RegimeType.RANGING == "RANGING"
    assert RegimeType.HIGH_VOLATILITY == "HIGH_VOLATILITY"
    assert RegimeType.LOW_VOLATILITY == "LOW_VOLATILITY"

    assert QuantGateStatus.PASS == "PASS"  # noqa: S105
    assert QuantGateStatus.WARNING == "WARNING"
    assert QuantGateStatus.FAIL == "FAIL"


def test_backtest_config_validation() -> None:
    """Verify BacktestConfig validation rules."""
    now = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    later = now + timedelta(days=30)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id="NIFTY_2026_JAN",
        start_time=now,
        end_time=later,
        initial_capital=Decimal("1000000"),
    )
    assert cfg.initial_capital == Decimal("1000000")
    assert cfg.base_currency == "INR"
    assert cfg.final_position_policy == FinalPositionPolicy.MARK_TO_MARKET
    assert cfg.conservative_same_bar_sl_first is True

    # Error: start_time >= end_time
    with pytest.raises(BacktestValidationError, match="must be strictly earlier"):
        BacktestConfig(
            strategy_id="AF_ORB_MOMENTUM_V1",
            strategy_version="1.0.0",
            dataset_id="NIFTY_2026_JAN",
            start_time=later,
            end_time=now,
            initial_capital=Decimal("1000000"),
        )

    # Error: initial_capital <= 0
    with pytest.raises(ValidationError):
        BacktestConfig(
            strategy_id="AF_ORB_MOMENTUM_V1",
            strategy_version="1.0.0",
            dataset_id="NIFTY_2026_JAN",
            start_time=now,
            end_time=later,
            initial_capital=Decimal("0"),
        )

    # Error: naive datetime
    with pytest.raises(BacktestValidationError, match="timezone-aware"):
        BacktestConfig(
            strategy_id="AF_ORB_MOMENTUM_V1",
            strategy_version="1.0.0",
            dataset_id="NIFTY_2026_JAN",
            start_time=datetime(2026, 1, 1, 9, 15),
            end_time=later,
            initial_capital=Decimal("1000000"),
        )

    # Error: empty string field
    with pytest.raises(BacktestValidationError, match="non-empty"):
        BacktestConfig(
            strategy_id="   ",
            strategy_version="1.0.0",
            dataset_id="NIFTY_2026_JAN",
            start_time=now,
            end_time=later,
            initial_capital=Decimal("1000000"),
        )


def test_backtest_trade_invariants() -> None:
    """Verify BacktestTrade temporal and quantity invariants."""
    t_entry = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    t_exit = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    trade = BacktestTrade(
        trade_id="TRD-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_timestamp=t_entry,
        entry_price=Decimal("24000"),
        entry_quantity=50,
        exit_timestamp=t_exit,
        exit_price=Decimal("24100"),
        exit_quantity=50,
        gross_pnl=Decimal("5000"),
        fees=Decimal("20"),
        slippage=Decimal("10"),
        net_pnl=Decimal("4970"),
        return_pct=Decimal("0.41"),
        holding_duration_seconds=1800,
        max_favorable_excursion=Decimal("120"),
        max_adverse_excursion=Decimal("10"),
        entry_signal_id="SIG-001",
        strategy_version="1.0.0",
        exit_reason="TARGET",
    )
    assert trade.net_pnl == Decimal("4970")

    # Error: exit precedes entry
    with pytest.raises(BacktestValidationError, match="cannot precede"):
        BacktestTrade(
            trade_id="TRD-002",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_timestamp=t_exit,
            entry_price=Decimal("24000"),
            entry_quantity=50,
            exit_timestamp=t_entry,
            exit_price=Decimal("24100"),
            exit_quantity=50,
            gross_pnl=Decimal("5000"),
            fees=Decimal("20"),
            slippage=Decimal("10"),
            net_pnl=Decimal("4970"),
            return_pct=Decimal("0.41"),
            holding_duration_seconds=1800,
            max_favorable_excursion=Decimal("120"),
            max_adverse_excursion=Decimal("10"),
            entry_signal_id="SIG-001",
            strategy_version="1.0.0",
            exit_reason="TARGET",
        )

    # Error: exit_qty > entry_qty
    with pytest.raises(BacktestValidationError, match="cannot exceed"):
        BacktestTrade(
            trade_id="TRD-003",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_timestamp=t_entry,
            entry_price=Decimal("24000"),
            entry_quantity=50,
            exit_timestamp=t_exit,
            exit_price=Decimal("24100"),
            exit_quantity=100,
            gross_pnl=Decimal("5000"),
            fees=Decimal("20"),
            slippage=Decimal("10"),
            net_pnl=Decimal("4970"),
            return_pct=Decimal("0.41"),
            holding_duration_seconds=1800,
            max_favorable_excursion=Decimal("120"),
            max_adverse_excursion=Decimal("10"),
            entry_signal_id="SIG-001",
            strategy_version="1.0.0",
            exit_reason="TARGET",
        )


def test_equity_snapshot_immutability() -> None:
    """Verify EquitySnapshot immutability."""
    snap = EquitySnapshot(
        timestamp=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
        equity=Decimal("1000000"),
        cash=Decimal("1000000"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        cumulative_fees=Decimal("0"),
        cumulative_slippage=Decimal("0"),
        notional_exposure=Decimal("0"),
        drawdown=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )
    with pytest.raises(ValidationError):
        snap.equity = Decimal("1005000")
