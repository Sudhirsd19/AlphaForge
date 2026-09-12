"""
Unit tests for alphaforge.backtest.portfolio.
Verifies derivatives accounting identities, single-entry model enforcement,
and continuous MFE/MAE tracking.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.backtest.portfolio import PortfolioTracker
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


def _create_candle(
    ts: datetime,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=100,
        source="NSE",
    )


def test_single_entry_invariant() -> None:
    """Verify single-entry model: opening while open raises BacktestValidationError."""
    tracker = PortfolioTracker(initial_capital=Decimal("1000000"))
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        reference_price=Decimal("24000"),
        effective_price=Decimal("24002"),
        timestamp=t0,
        signal_id="SIG-001",
        strategy_version="1.0.0",
        fee=Decimal("20"),
        slippage_loss=Decimal("100"),
    )
    assert tracker.has_open_position is True

    # Error: cannot open second position
    with pytest.raises(BacktestValidationError, match="Single-entry model violation"):
        tracker.open_position(
            symbol="NIFTY",
            side=TradeSide.SHORT,
            quantity=50,
            reference_price=Decimal("24010"),
            effective_price=Decimal("24012"),
            timestamp=t0 + timedelta(minutes=3),
            signal_id="SIG-002",
            strategy_version="1.0.0",
            fee=Decimal("20"),
            slippage_loss=Decimal("100"),
        )


def test_close_without_open_position() -> None:
    """Verify closing without an open position raises BacktestValidationError."""
    tracker = PortfolioTracker(initial_capital=Decimal("1000000"))
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    with pytest.raises(BacktestValidationError, match="No position is currently open"):
        tracker.close_position(
            reference_exit_price=Decimal("24050"),
            effective_exit_price=Decimal("24048"),
            timestamp=t0,
            exit_reason="STOP_LOSS",
            exit_fee=Decimal("20"),
            exit_slippage_loss=Decimal("100"),
        )


def test_derivatives_accounting_identity() -> None:
    """Verify Equity == Cash + Margin_Used + Unrealized_PnL holds at all times."""
    tracker = PortfolioTracker(
        initial_capital=Decimal("1000000"),
        margin_rate=Decimal("0.10"),
    )
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    # Initial state
    assert tracker.equity == tracker.cash == Decimal("1000000")

    # Open LONG position
    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        reference_price=Decimal("24000"),
        effective_price=Decimal("24002"),
        timestamp=t0,
        signal_id="SIG-001",
        strategy_version="1.0.0",
        fee=Decimal("25"),
        slippage_loss=Decimal("100"),
    )

    # Mark-to-market at bar close
    c1 = _create_candle(
        ts=t0 + timedelta(minutes=3),
        open_p=Decimal("24000"),
        high_p=Decimal("24050"),
        low_p=Decimal("23990"),
        close_p=Decimal("24030"),
    )
    snap1 = tracker.update_bar(c1)

    # Verify identity: Equity == Cash + Margin_Used + Unrealized_PnL
    expected_equity = snap1.cash + snap1.margin_used + snap1.unrealized_pnl
    assert snap1.equity == expected_equity

    # Close position
    trade = tracker.close_position(
        reference_exit_price=Decimal("24030"),
        effective_exit_price=Decimal("24028"),
        timestamp=t0 + timedelta(minutes=6),
        exit_reason="TARGET",
        exit_fee=Decimal("25"),
        exit_slippage_loss=Decimal("100"),
    )
    assert trade.net_pnl <= trade.gross_pnl
    assert tracker.margin_used == Decimal("0")
    assert tracker.equity == tracker.cash


def test_continuous_mfe_mae_tracking() -> None:
    """Verify MFE and MAE are correctly accumulated from candle high and low."""
    tracker = PortfolioTracker(initial_capital=Decimal("1000000"))
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        reference_price=Decimal("24000"),
        effective_price=Decimal("24000"),
        timestamp=t0,
        signal_id="SIG-001",
        strategy_version="1.0.0",
        fee=Decimal("0"),
        slippage_loss=Decimal("0"),
    )

    # Candle 1: High = 24040 (+40), Low = 23980 (-20)
    c1 = _create_candle(
        t0 + timedelta(minutes=3),
        Decimal("24000"),
        Decimal("24040"),
        Decimal("23980"),
        Decimal("24010"),
    )
    tracker.update_bar(c1)
    assert tracker.mfe == Decimal("40")
    assert tracker.mae == Decimal("20")

    # Candle 2: High = 24060 (+60), Low = 24005 (no new MAE)
    c2 = _create_candle(
        t0 + timedelta(minutes=6),
        Decimal("24010"),
        Decimal("24060"),
        Decimal("24005"),
        Decimal("24050"),
    )
    tracker.update_bar(c2)
    assert tracker.mfe == Decimal("60")
    assert tracker.mae == Decimal("20")

    trade = tracker.close_position(
        reference_exit_price=Decimal("24050"),
        effective_exit_price=Decimal("24050"),
        timestamp=t0 + timedelta(minutes=9),
        exit_reason="TARGET",
        exit_fee=Decimal("0"),
        exit_slippage_loss=Decimal("0"),
    )
    assert trade.max_favorable_excursion == Decimal("60")
    assert trade.max_adverse_excursion == Decimal("20")
