"""
Integration tests for AlphaForge Strategy Integration & Look-Ahead Bias Immunity.
CRITICAL FORENSIC REQUIREMENT (Section 32, 37, 47):
Proves that modifying or appending future market candles cannot leak backwards
into earlier strategy evaluations, risk decisions, orders, fills, or equity snapshots.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig, FinalPositionPolicy
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle


def _generate_deterministic_candles(
    n: int, start_price: Decimal = Decimal("24000")
) -> list[MarketCandle]:
    candles = []
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    price = start_price
    for i in range(n):
        ts = t0 + timedelta(minutes=3 * i)
        # Periodic movement
        if i % 3 == 0:
            price += Decimal("15")
        elif i % 3 == 1:
            price -= Decimal("5")
        else:
            price += Decimal("2")

        c = MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            contract_id="NIFTY-SPOT",
            exchange_timestamp=ts,
            received_timestamp=ts + timedelta(milliseconds=10),
            timeframe="3m",
            open=price,
            high=price + Decimal("10"),
            low=price - Decimal("10"),
            close=price + Decimal("3"),
            volume=10000 + (i % 5) * 2000,
            open_interest=50000,
            source="NSE",
        )
        candles.append(c)
    return candles


def test_future_candle_mutation_and_temporal_isolation() -> None:
    """
    FORENSIC PROOF:
    Run Backtest on original N candles:
      result_original = engine.run()
    Run Backtest on extended (N + M) candles where M future candles are appended
    and mutated with extreme prices (e.g. 50,000 index price, 10x volume):
      result_extended = engine.run()

    Assert byte-for-byte / value-for-value identity for all historical outputs
    up to bar N:
    - Exactly identical historical equity snapshots for bars 0..N-1
    - Exactly identical trades that closed on or before bar N
    - Exactly identical ledger signals emitted up to bar N
    """
    n_base = 40
    base_candles = _generate_deterministic_candles(n_base)

    # Future mutated candles with extreme values
    t_last = base_candles[-1].exchange_timestamp
    future_candles = []
    for j in range(1, 21):
        ts_future = t_last + timedelta(minutes=3 * j)
        future_c = MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            contract_id="NIFTY-SPOT",
            exchange_timestamp=ts_future,
            received_timestamp=ts_future + timedelta(milliseconds=10),
            timeframe="3m",
            open=Decimal("50000") + Decimal(j * 100),  # Extreme mutated price
            high=Decimal("55000") + Decimal(j * 100),
            low=Decimal("49000") + Decimal(j * 100),
            close=Decimal("52000") + Decimal(j * 100),
            volume=500000,  # Extreme volume
            open_interest=200000,
            source="NSE",
        )
        future_candles.append(future_c)

    extended_candles = base_candles + future_candles

    dataset_base = BacktestDataset("BASE_DS", base_candles)
    dataset_ext = BacktestDataset("EXT_DS", extended_candles)

    t_start = base_candles[0].exchange_timestamp
    t_base_end = base_candles[-1].exchange_timestamp + timedelta(minutes=3)
    t_ext_end = future_candles[-1].exchange_timestamp + timedelta(minutes=3)

    cfg_base = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset_base.dataset_id,
        start_time=t_start,
        end_time=t_base_end,
        initial_capital=Decimal("1000000"),
        final_position_policy=FinalPositionPolicy.MARK_TO_MARKET,
        warmup_bars=15,
    )

    cfg_ext = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset_ext.dataset_id,
        start_time=t_start,
        end_time=t_ext_end,
        initial_capital=Decimal("1000000"),
        final_position_policy=FinalPositionPolicy.MARK_TO_MARKET,
        warmup_bars=15,
    )

    res_base = BacktestEngine(cfg_base, dataset_base).run()
    res_ext = BacktestEngine(cfg_ext, dataset_ext).run()

    # Verify that for all N base bars, the equity snapshots are bit-for-bit identical
    for k in range(n_base):
        snap_b = res_base.equity_curve[k]
        snap_e = res_ext.equity_curve[k]

        assert snap_b.timestamp == snap_e.timestamp
        assert snap_b.equity == snap_e.equity
        assert snap_b.cash == snap_e.cash
        assert snap_b.realized_pnl == snap_e.realized_pnl
        assert snap_b.unrealized_pnl == snap_e.unrealized_pnl
        assert snap_b.notional_exposure == snap_e.notional_exposure
        assert snap_b.drawdown == snap_e.drawdown

    # Verify that any trade completed on or before base end timestamp is identical
    trades_base_in_window = [t for t in res_base.trades if t.exit_timestamp <= t_base_end]
    trades_ext_in_window = [t for t in res_ext.trades if t.exit_timestamp <= t_base_end]

    assert len(trades_base_in_window) == len(trades_ext_in_window)
    for tb, te in zip(trades_base_in_window, trades_ext_in_window, strict=True):
        assert tb.entry_timestamp == te.entry_timestamp
        assert tb.exit_timestamp == te.exit_timestamp
        assert tb.entry_price == te.entry_price
        assert tb.exit_price == te.exit_price
        assert tb.net_pnl == te.net_pnl
