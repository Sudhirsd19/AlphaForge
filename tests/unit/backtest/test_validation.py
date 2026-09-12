"""
Unit tests for alphaforge.backtest.validation.
Verifies Quant Gates A-J audits, overfitting diagnostics, OOS partitioning,
walk-forward generation, and market regime analysis.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.models import (
    BacktestMetrics,
    BacktestTrade,
    EquitySnapshot,
    QuantGateStatus,
    RegimeType,
    ValidationStatus,
)
from alphaforge.backtest.validation import (
    DatasetPartitioner,
    MarketRegimeAnalyzer,
    QuantGateEvaluator,
    WalkForwardEngine,
)
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


def _create_candle(ts: datetime, close_p: Decimal) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=close_p,
        high=close_p + Decimal("10"),
        low=close_p - Decimal("10"),
        close=close_p,
        volume=100,
        source="NSE",
    )


def test_quant_gate_evaluator_all_pass() -> None:
    """Verify all gates pass on valid backtest execution."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset(
        "DS",
        [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(50)],
    )

    trades = [
        BacktestTrade(
            trade_id=f"T{i}",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_timestamp=t0 + timedelta(hours=i),
            entry_price=Decimal("24000"),
            entry_quantity=50,
            exit_timestamp=t0 + timedelta(hours=i, minutes=30),
            exit_price=Decimal("24050"),
            exit_quantity=50,
            gross_pnl=Decimal("2500"),
            fees=Decimal("20"),
            slippage=Decimal("10"),
            net_pnl=Decimal("2470"),
            return_pct=Decimal("0.2"),
            holding_duration_seconds=1800,
            max_favorable_excursion=Decimal("50"),
            max_adverse_excursion=Decimal("5"),
            entry_signal_id=f"SIG-{i}",
            strategy_version="1.0.0",
            exit_reason="TARGET",
        )
        for i in range(35)  # >= 30 trades for Gate J!
    ]

    equity_curve = [
        EquitySnapshot(
            timestamp=t0 + timedelta(hours=i),
            equity=Decimal("1000000") + Decimal(i * 100),
            cash=Decimal("1000000") + Decimal(i * 100),
            realized_pnl=Decimal(i * 100),
            unrealized_pnl=Decimal("0"),
            cumulative_fees=Decimal("0"),
            cumulative_slippage=Decimal("0"),
            notional_exposure=Decimal("0"),
            drawdown=Decimal("0"),
            drawdown_pct=Decimal("0"),
        )
        for i in range(35)
    ]

    metrics = BacktestMetrics(
        total_return=Decimal("86450"),
        total_return_pct=Decimal("8.64"),
        total_trades=35,
        winning_trades=35,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("1"),
        loss_rate=Decimal("0"),
        average_win=Decimal("2470"),
        average_loss=Decimal("0"),
        expectancy=Decimal("2470"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("87500"),
        total_fees=Decimal("700"),
        total_slippage=Decimal("350"),
        net_pnl=Decimal("86450"),
    )

    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
    )

    assert status == ValidationStatus.VALID
    assert len(errs) == 0
    assert all(g.status == QuantGateStatus.PASS for g in gates)


def test_quant_gate_j_insufficient_sample_warning() -> None:
    """Verify Gate J issues warning when trade count < 30."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])

    metrics = BacktestMetrics(
        total_return=Decimal("100"),
        total_return_pct=Decimal("0.01"),
        total_trades=5,  # < 30
        winning_trades=3,
        losing_trades=2,
        break_even_trades=0,
        win_rate=Decimal("0.6"),
        loss_rate=Decimal("0.4"),
        average_win=Decimal("100"),
        average_loss=Decimal("100"),
        expectancy=Decimal("20"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("100"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("100"),
    )

    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
    )
    # Trade count < 30 triggers WARNING on Gate J
    gate_j = next(g for g in gates if g.gate_id == "Gate J")
    assert gate_j.status == QuantGateStatus.WARNING
    assert status == ValidationStatus.WARNING


def test_dataset_partitioner_non_overlapping() -> None:
    """Verify train/val/test splits have non-overlapping bounds."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(100)]
    dataset = BacktestDataset("DS_100", candles)

    train, val, test = DatasetPartitioner.split_train_val_test(dataset)
    assert len(train) == 60
    assert len(val) == 20
    assert len(test) == 20

    # Ensure no temporal overlap
    assert train.candles[-1].exchange_timestamp < val.candles[0].exchange_timestamp
    assert val.candles[-1].exchange_timestamp < test.candles[0].exchange_timestamp


def test_walk_forward_engine() -> None:
    """Verify rolling walk-forward fold generation."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(100)]
    dataset = BacktestDataset("DS_100", candles)

    folds = WalkForwardEngine.generate_rolling_folds(
        dataset, train_bars=40, test_bars=10, step_bars=10
    )
    assert len(folds) == 6
    for f in folds:
        assert len(f.train_dataset) == 40
        assert len(f.test_dataset) == 10
        train_last_ts = f.train_dataset.candles[-1].exchange_timestamp
        test_first_ts = f.test_dataset.candles[0].exchange_timestamp
        assert train_last_ts < test_first_ts


def test_market_regime_analyzer() -> None:
    """Verify regime classification based only on past candles."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    # Trending upwards
    bull_candles = [
        _create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000") + Decimal(i * 10))
        for i in range(30)
    ]
    regime = MarketRegimeAnalyzer.classify_candle_regime(bull_candles)
    assert regime in (RegimeType.TRENDING_BULL, RegimeType.HIGH_VOLATILITY)
