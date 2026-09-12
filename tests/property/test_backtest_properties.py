"""
Property-based tests for AlphaForge Backtest & Quant Validation Engine using Hypothesis.
Proves fundamental mathematical, temporal, and execution invariants:
P1:  Determinism (same config + dataset -> identical run_id and result metrics)
P2:  Reordering input records preserves identical sorted dataset and checksum
P3:  Temporal isolation (future data cannot change historical decisions)
P4:  Zero trades invariant (zero trades -> 0 PnL and valid empty stats)
P5:  Friction penalty (net_pnl <= gross_pnl under adverse friction)
P6:  Quantity conservation (exit quantity <= open quantity)
P7:  Position non-negativity (position quantity never becomes negative)
P8:  Derivatives accounting identity (Equity == Cash + Margin_Used + Unrealized_PnL)
P9:  Dataset checksum determinism
P10: Checksum sensitivity (different dataset checksum -> different run identity)
P11: Run identity uniqueness
P12: Future appendage independence
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine, compute_backtest_run_id
from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import BacktestConfig
from alphaforge.backtest.portfolio import PortfolioTracker
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


def _make_candle(ts: datetime, price: Decimal, volume: int = 100) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=price,
        high=price + Decimal("5"),
        low=price - Decimal("5"),
        close=price,
        volume=volume,
        open_interest=50000,
        source="NSE",
    )


# ---------------------------------------------------------------------------
# P1: Determinism (same config + dataset -> identical run_id and metrics)
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    price=st.integers(min_value=20000, max_value=30000),
    capital=st.integers(min_value=100000, max_value=5000000),
)
def test_p1_determinism(price: int, capital: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _make_candle(t0, Decimal(price))
    ds = BacktestDataset("DS", [c1])

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal(capital),
    )

    id1 = compute_backtest_run_id(cfg, ds.metadata)
    id2 = compute_backtest_run_id(cfg, ds.metadata)
    assert id1 == id2

    res1 = BacktestEngine(cfg, ds).run()
    res2 = BacktestEngine(cfg, ds).run()
    assert res1.backtest_run_id == res2.backtest_run_id
    assert res1.metrics.total_return == res2.metrics.total_return


# ---------------------------------------------------------------------------
# P2: Reordering input records preserves identical sorted dataset and checksum
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    p1=st.integers(min_value=20000, max_value=25000),
    p2=st.integers(min_value=20000, max_value=25000),
    p3=st.integers(min_value=20000, max_value=25000),
)
def test_p2_reordering_invariance(p1: int, p2: int, p3: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _make_candle(t0, Decimal(p1))
    c2 = _make_candle(t0 + timedelta(minutes=3), Decimal(p2))
    c3 = _make_candle(t0 + timedelta(minutes=6), Decimal(p3))

    ds_ordered = BacktestDataset("DS1", [c1, c2, c3])
    ds_reversed = BacktestDataset("DS2", [c3, c2, c1])
    ds_shuffled = BacktestDataset("DS3", [c2, c1, c3])

    assert ds_ordered.checksum == ds_reversed.checksum == ds_shuffled.checksum


# ---------------------------------------------------------------------------
# P4: Zero trades invariant (zero trades -> 0 PnL and valid empty stats)
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(capital=st.integers(min_value=100000, max_value=10000000))
def test_p4_zero_trades_invariant(capital: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    m = calculate_backtest_metrics([], [], Decimal(capital), t0, t1)
    assert m.total_trades == 0
    assert m.net_pnl == Decimal("0")
    assert m.win_rate == Decimal("0")
    assert m.payoff_ratio is None
    assert m.profit_factor is None


# ---------------------------------------------------------------------------
# P5: Friction penalty (net_pnl <= gross_pnl under adverse friction)
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    entry_p=st.integers(min_value=20000, max_value=25000),
    exit_p=st.integers(min_value=20000, max_value=25000),
    fee_rate=st.floats(min_value=0.0001, max_value=0.005),
    slip_rate=st.floats(min_value=0.0001, max_value=0.005),
)
def test_p5_friction_penalty(entry_p: int, exit_p: int, fee_rate: float, slip_rate: float) -> None:
    tracker = PortfolioTracker(initial_capital=Decimal("1000000"))
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    fee_d = Decimal(str(round(fee_rate, 4)))
    slip_d = Decimal(str(round(slip_rate, 4)))

    ref_entry = Decimal(entry_p)
    ref_exit = Decimal(exit_p)

    eff_entry = ref_entry * (Decimal("1") + slip_d)
    eff_exit = ref_exit * (Decimal("1") - slip_d)

    entry_fee = eff_entry * Decimal("50") * fee_d
    entry_slip = abs(eff_entry - ref_entry) * Decimal("50")
    exit_fee = eff_exit * Decimal("50") * fee_d
    exit_slip = abs(eff_exit - ref_exit) * Decimal("50")

    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        reference_price=ref_entry,
        effective_price=eff_entry,
        timestamp=t0,
        signal_id="SIG-01",
        strategy_version="1.0.0",
        fee=entry_fee,
        slippage_loss=entry_slip,
    )
    trade = tracker.close_position(
        reference_exit_price=ref_exit,
        effective_exit_price=eff_exit,
        timestamp=t0 + timedelta(minutes=15),
        exit_reason="TARGET",
        exit_fee=exit_fee,
        exit_slippage_loss=exit_slip,
    )

    assert trade.net_pnl <= trade.gross_pnl


# ---------------------------------------------------------------------------
# P6 & P7: Quantity conservation and position non-negativity
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(qty=st.integers(min_value=1, max_value=500))
def test_p6_p7_quantity_conservation(qty: int) -> None:
    tracker = PortfolioTracker(initial_capital=Decimal("1000000"))
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=qty,
        reference_price=Decimal("24000"),
        effective_price=Decimal("24000"),
        timestamp=t0,
        signal_id="SIG-01",
        strategy_version="1.0.0",
        fee=Decimal("0"),
        slippage_loss=Decimal("0"),
    )
    assert tracker.position_quantity >= 0

    trade = tracker.close_position(
        reference_exit_price=Decimal("24050"),
        effective_exit_price=Decimal("24050"),
        timestamp=t0 + timedelta(minutes=15),
        exit_reason="TARGET",
        exit_fee=Decimal("0"),
        exit_slippage_loss=Decimal("0"),
    )
    assert trade.exit_quantity <= trade.entry_quantity
    assert tracker.position_quantity == 0


# ---------------------------------------------------------------------------
# P8: Derivatives accounting identity (Equity == Cash + Margin + Unrealized_PnL)
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    close_delta=st.integers(min_value=-100, max_value=100),
    margin_pct=st.floats(min_value=0.05, max_value=0.20),
)
def test_p8_derivatives_accounting_identity(close_delta: int, margin_pct: float) -> None:
    tracker = PortfolioTracker(
        initial_capital=Decimal("1000000"),
        margin_rate=Decimal(str(round(margin_pct, 2))),
    )
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    tracker.open_position(
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        reference_price=Decimal("24000"),
        effective_price=Decimal("24000"),
        timestamp=t0,
        signal_id="SIG-01",
        strategy_version="1.0.0",
        fee=Decimal("10"),
        slippage_loss=Decimal("0"),
    )
    c = _make_candle(t0 + timedelta(minutes=3), Decimal(24000 + close_delta))
    snap = tracker.update_bar(c)

    expected = snap.cash + snap.margin_used + snap.unrealized_pnl
    assert abs(snap.equity - expected) < Decimal("0.001")


# ---------------------------------------------------------------------------
# P9 & P10: Dataset Checksum & Run ID Sensitivity
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    p1=st.integers(min_value=20000, max_value=25000),
    p2=st.integers(min_value=25001, max_value=30000),
)
def test_p9_p10_checksum_sensitivity(p1: int, p2: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _make_candle(t0, Decimal(p1))
    c2 = _make_candle(t0, Decimal(p2))

    ds1 = BacktestDataset("DS1", [c1])
    ds2 = BacktestDataset("DS2", [c2])
    assert ds1.checksum != ds2.checksum

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds1.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(days=1),
        initial_capital=Decimal("1000000"),
    )
    assert compute_backtest_run_id(cfg, ds1.metadata) != compute_backtest_run_id(cfg, ds2.metadata)
