"""
Unit tests for alphaforge.backtest.metrics.
Verifies trade statistics, profit factor, expectancy, and insufficient-sample handling.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import BacktestTrade, EquitySnapshot
from alphaforge.risk.enums import TradeSide


def _create_dummy_trade(trade_id: str, net_pnl: Decimal) -> BacktestTrade:
    t0 = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=15)
    return BacktestTrade(
        trade_id=trade_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_timestamp=t0,
        entry_price=Decimal("24000"),
        entry_quantity=50,
        exit_timestamp=t1,
        exit_price=Decimal("24020") if net_pnl > 0 else Decimal("23980"),
        exit_quantity=50,
        gross_pnl=net_pnl + Decimal("20"),
        fees=Decimal("20"),
        slippage=Decimal("0"),
        net_pnl=net_pnl,
        return_pct=Decimal("0.1"),
        holding_duration_seconds=900,
        max_favorable_excursion=Decimal("20"),
        max_adverse_excursion=Decimal("5"),
        entry_signal_id="SIG-01",
        strategy_version="1.0.0",
        exit_reason="TARGET" if net_pnl > 0 else "STOP_LOSS",
    )


def test_zero_trades_metrics() -> None:
    """Verify metrics calculation when zero trades occurred."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=5)
    snap = EquitySnapshot(
        timestamp=t0,
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

    metrics = calculate_backtest_metrics(
        trades=[],
        equity_curve=[snap],
        initial_capital=Decimal("1000000"),
        start_time=t0,
        end_time=t1,
    )

    assert metrics.total_trades == 0
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 0
    assert metrics.win_rate == Decimal("0")
    assert metrics.profit_factor is None
    assert metrics.payoff_ratio is None
    assert metrics.annualized_return_pct is None
    assert metrics.sharpe_ratio is None


def test_all_winning_and_all_losing_trades() -> None:
    """Verify edge case of 100% win rate and 100% loss rate."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=10)

    # 1. All winning
    w1 = _create_dummy_trade("T1", Decimal("5000"))
    w2 = _create_dummy_trade("T2", Decimal("3000"))
    m_win = calculate_backtest_metrics(
        trades=[w1, w2],
        equity_curve=[],
        initial_capital=Decimal("1000000"),
        start_time=t0,
        end_time=t1,
    )
    assert m_win.win_rate == Decimal("1")
    assert m_win.loss_rate == Decimal("0")
    assert m_win.average_loss == Decimal("0")
    assert m_win.payoff_ratio is None  # Zero losses -> payoff is None
    assert m_win.profit_factor is None  # Zero losses -> profit factor is None

    # 2. All losing
    l1 = _create_dummy_trade("T3", Decimal("-2000"))
    l2 = _create_dummy_trade("T4", Decimal("-4000"))
    m_loss = calculate_backtest_metrics(
        trades=[l1, l2],
        equity_curve=[],
        initial_capital=Decimal("1000000"),
        start_time=t0,
        end_time=t1,
    )
    assert m_loss.win_rate == Decimal("0")
    assert m_loss.loss_rate == Decimal("1")
    assert m_loss.average_win == Decimal("0")
    assert m_loss.profit_factor == Decimal("0")


def test_insufficient_sample_guards() -> None:
    """Verify that samples under 30 days return None for annualized ratios."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=15)  # Under 30 days!

    trade = _create_dummy_trade("T1", Decimal("10000"))
    snaps = [
        EquitySnapshot(
            timestamp=t0 + timedelta(hours=i),
            equity=Decimal("1000000") + Decimal(i * 100),
            cash=Decimal("1000000"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            cumulative_fees=Decimal("0"),
            cumulative_slippage=Decimal("0"),
            notional_exposure=Decimal("0"),
            drawdown=Decimal("0"),
            drawdown_pct=Decimal("0"),
        )
        for i in range(35)
    ]

    metrics = calculate_backtest_metrics(
        trades=[trade],
        equity_curve=snaps,
        initial_capital=Decimal("1000000"),
        start_time=t0,
        end_time=t1,
    )

    # Must be None due to insufficient duration (< 30 days)
    assert metrics.annualized_return_pct is None
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None
    assert metrics.calmar_ratio is None
