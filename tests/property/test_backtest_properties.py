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

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.backtest.datasets import SENTINEL_EMPTY_DATETIME, BacktestDataset
from alphaforge.backtest.engine import BacktestEngine, compute_backtest_run_id
from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import (
    BacktestConfig,
    QuantGateStatus,
    ValidationStatus,
)
from alphaforge.backtest.portfolio import PortfolioTracker
from alphaforge.backtest.validation import (
    ParameterIsolationWorkflow,
    WalkForwardEngine,
)
from alphaforge.contract.models import ContractMaster
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.enums import TradeSide
from alphaforge.risk.models import RiskConfig
from alphaforge.strategy.config import StrategyConfig


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


# ---------------------------------------------------------------------------
# P11: Run identity uniqueness
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(delta_m=st.integers(min_value=1, max_value=60))
def test_p11_run_identity_uniqueness(delta_m: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c1])
    cfg1 = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
    )
    cfg2 = cfg1.model_copy(update={"end_time": t0 + timedelta(minutes=30 + delta_m)})
    assert compute_backtest_run_id(cfg1, ds.metadata) != compute_backtest_run_id(cfg2, ds.metadata)


# ---------------------------------------------------------------------------
# P12: Future appendage independence
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(future_price=st.integers(min_value=30000, max_value=50000))
def test_p12_future_appendage_independence(future_price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles_base = [
        _make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)
    ]
    future_c = _make_candle(t0 + timedelta(minutes=30), Decimal(future_price), volume=50000)

    ds_base = BacktestDataset("DS_BASE", candles_base)
    ds_ext = BacktestDataset("DS_EXT", candles_base + [future_c])

    cfg_base = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds_base.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    cfg_ext = cfg_base.model_copy(
        update={
            "dataset_id": ds_ext.dataset_id,
            "end_time": t0 + timedelta(minutes=33),
        }
    )

    res_base = BacktestEngine(cfg_base, ds_base).run()
    res_ext = BacktestEngine(cfg_ext, ds_ext).run()

    # Historical snapshots in base horizon must be identical
    for k in range(len(candles_base)):
        assert res_base.equity_curve[k].equity == res_ext.equity_curve[k].equity
        assert res_base.equity_curve[k].cash == res_ext.equity_curve[k].cash


# ---------------------------------------------------------------------------
# P13: Risk config sensitivity
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(max_trades=st.integers(min_value=1, max_value=20))
def test_p13_risk_config_sensitivity(max_trades: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c])
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )
    id_base = compute_backtest_run_id(cfg, ds.metadata)
    risk_diff = RiskConfig(max_open_trades=max_trades + 25)
    id_diff = compute_backtest_run_id(cfg, ds.metadata, risk_config=risk_diff)
    assert id_base != id_diff


# ---------------------------------------------------------------------------
# P14: Strategy config sensitivity
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(atr_p=st.integers(min_value=5, max_value=50))
def test_p14_strategy_config_sensitivity(atr_p: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c])
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )
    id_base = compute_backtest_run_id(cfg, ds.metadata)
    strat_diff = StrategyConfig(atr_period=atr_p + 100)
    id_diff = compute_backtest_run_id(cfg, ds.metadata, strategy_config=strat_diff)
    assert id_base != id_diff


# ---------------------------------------------------------------------------
# P15: Contract master sensitivity
# ---------------------------------------------------------------------------
def test_p15_contract_master_sensitivity() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c])
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )
    id_none = compute_backtest_run_id(cfg, ds.metadata, contract_master=None)
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        expiry_datetime=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2026, 6, 1, 9, 15, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 9, 15, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
        lot_size=50,
        tick_size=Decimal("0.05"),
        data_source="NSE",
    )
    id_contract = compute_backtest_run_id(cfg, ds.metadata, contract_master=contract)
    assert id_none != id_contract


# ---------------------------------------------------------------------------
# P16: Cost config sensitivity
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(fee_rate=st.floats(min_value=0.001, max_value=0.01))
def test_p16_cost_config_sensitivity(fee_rate: float) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c])
    cfg_base = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )
    id_base = compute_backtest_run_id(cfg_base, ds.metadata)
    cfg_diff = cfg_base.model_copy(
        update={"cost_config": CostConfig(entry_fee_rate=Decimal(str(round(fee_rate, 4))))}
    )
    id_diff = compute_backtest_run_id(cfg_diff, ds.metadata)
    assert id_base != id_diff


# ---------------------------------------------------------------------------
# P17: Engine version sensitivity
# ---------------------------------------------------------------------------
def test_p17_engine_version_sensitivity() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _make_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS", [c])
    cfg1 = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
        engine_version="PHASE10_BACKTEST_V1",
    )
    cfg2 = cfg1.model_copy(update={"engine_version": "PHASE10_BACKTEST_V2"})
    assert compute_backtest_run_id(cfg1, ds.metadata) != compute_backtest_run_id(cfg2, ds.metadata)


# ---------------------------------------------------------------------------
# P18: Empty dataset determinism & sentinel metadata
# ---------------------------------------------------------------------------
def test_p18_empty_dataset_determinism_and_sentinel() -> None:
    ds_empty = BacktestDataset("EMPTY_DS", [])
    assert ds_empty.metadata.start_timestamp == SENTINEL_EMPTY_DATETIME
    assert ds_empty.metadata.end_timestamp == SENTINEL_EMPTY_DATETIME
    assert ds_empty.metadata.record_count == 0

    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds_empty.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )
    res1 = BacktestEngine(cfg, ds_empty).run()
    res2 = BacktestEngine(cfg, ds_empty).run()

    assert res1.backtest_run_id == res2.backtest_run_id
    assert len(res1.trades) == 0
    assert len(res1.equity_curve) == 0
    assert res1.metrics.total_trades == 0


# ---------------------------------------------------------------------------
# P19: Dual-run canonical hash reproducibility
# ---------------------------------------------------------------------------
def test_p19_dual_run_canonical_hash_reproducibility() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    ds = BacktestDataset("DS_REPRO", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_reproducibility=True,
    )
    res = BacktestEngine(cfg, ds).run()
    gate_g = next(g for g in res.quant_gates if g.gate_id == "Gate G")
    assert gate_g.evidence["rerun_matched"] is True
    assert gate_g.evidence["mismatches"] == 0
    assert gate_g.evidence["run_1_canonical_hash"] == gate_g.evidence["run_2_canonical_hash"]
    assert gate_g.evidence["run_1_canonical_hash"] != "NONE"


# ---------------------------------------------------------------------------
# P20: OOS parameter immutability
# ---------------------------------------------------------------------------
def test_p20_oos_parameter_immutability() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(50)]
    ds = BacktestDataset("DS_OOS", candles)
    params = {"rsi_period": 14, "target_multiple": "2.0"}
    folds = WalkForwardEngine.generate_rolling_folds(
        dataset=ds,
        train_bars=20,
        val_bars=10,
        test_bars=10,
        step_bars=10,
        parameters=params,
    )
    assert len(folds) > 0
    f = folds[0]
    with pytest.raises(TypeError):
        f.frozen_parameters["mutated"] = True  # type: ignore[index]


# ---------------------------------------------------------------------------
# P21: Future trace mutation invariance
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(
    base_price=st.integers(min_value=23000, max_value=25000),
    mutated_future_price=st.integers(min_value=26000, max_value=30000),
)
def test_p21_future_trace_mutation_invariance(base_price: int, mutated_future_price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    prefix_candles = [
        _make_candle(t0 + timedelta(minutes=3 * i), Decimal(base_price)) for i in range(10)
    ]
    cutoff_ts = prefix_candles[-1].exchange_timestamp

    d1_candles = list(prefix_candles) + [
        _make_candle(t0 + timedelta(minutes=3 * (10 + j)), Decimal(base_price)) for j in range(5)
    ]
    d2_candles = list(prefix_candles) + [
        _make_candle(t0 + timedelta(minutes=3 * (10 + j)), Decimal(mutated_future_price))
        for j in range(5)
    ]

    ds1 = BacktestDataset("DS1", d1_candles)
    ds2 = BacktestDataset("DS2", d2_candles)

    cfg1 = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds1.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=2),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    cfg2 = cfg1.model_copy(update={"dataset_id": ds2.dataset_id})

    eng1 = BacktestEngine(cfg1, ds1)
    eng1.run()

    eng2 = BacktestEngine(cfg2, ds2)
    eng2.run()

    trace1_cutoff = [e for e in eng1.trace if e.timestamp <= cutoff_ts]
    trace2_cutoff = [e for e in eng2.trace if e.timestamp <= cutoff_ts]

    assert len(trace1_cutoff) == len(trace2_cutoff)
    for e1, e2 in zip(trace1_cutoff, trace2_cutoff, strict=True):
        assert e1.timestamp == e2.timestamp
        assert e1.event_type == e2.event_type
        assert e1.price == e2.price
        assert e1.entity_id == e2.entity_id


# ---------------------------------------------------------------------------
# P22: Result canonical hash determinism
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(price=st.integers(min_value=22000, max_value=26000))
def test_p22_result_canonical_hash_determinism(price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal(price)) for i in range(10)]
    ds = BacktestDataset("DS_P22", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    res1 = BacktestEngine(cfg, ds).run()
    res2 = BacktestEngine(cfg, ds).run()
    assert res1.result_canonical_hash is not None
    assert res1.result_canonical_hash == res2.result_canonical_hash


# ---------------------------------------------------------------------------
# P23: Trace canonical hash determinism
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(price=st.integers(min_value=22000, max_value=26000))
def test_p23_trace_canonical_hash_determinism(price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal(price)) for i in range(10)]
    ds = BacktestDataset("DS_P23", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    res1 = BacktestEngine(cfg, ds).run()
    res2 = BacktestEngine(cfg, ds).run()
    assert res1.trace_canonical_hash is not None
    assert res1.trace_canonical_hash == res2.trace_canonical_hash


# ---------------------------------------------------------------------------
# P24: Validation warning event emission
# ---------------------------------------------------------------------------
def test_p24_validation_warning_event_emission() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    ds = BacktestDataset("DS_P24", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    ledger = AuditLedger(storage=InMemoryLedgerStorage())
    engine = BacktestEngine(cfg, ds, ledger=ledger)
    res = engine.run()

    events = ledger.get_events_by_correlation_id(res.backtest_run_id)
    types = [e.event_type for e in events]
    assert AuditEventType.VALIDATION_WARNING in types
    assert AuditEventType.BACKTEST_COMPLETED in types
    assert types.index(AuditEventType.VALIDATION_WARNING) < types.index(
        AuditEventType.BACKTEST_COMPLETED
    )


# ---------------------------------------------------------------------------
# P25: Validation failure event emission
# ---------------------------------------------------------------------------
def test_p25_validation_failure_event_emission() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(5)]
    ds = BacktestDataset("DS_P25", candles)
    object.__setattr__(ds, "_candles", (candles[2], candles[1], candles[0]))

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    ledger = AuditLedger(storage=InMemoryLedgerStorage())
    engine = BacktestEngine(cfg, ds, ledger=ledger)
    res = engine.run()

    events = ledger.get_events_by_correlation_id(res.backtest_run_id)
    types = [e.event_type for e in events]
    assert AuditEventType.VALIDATION_FAILED in types
    assert AuditEventType.BACKTEST_COMPLETED in types
    assert types.index(AuditEventType.VALIDATION_FAILED) < types.index(
        AuditEventType.BACKTEST_COMPLETED
    )


# ---------------------------------------------------------------------------
# P26: OOS parameter isolation
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(mutated_price=st.integers(min_value=30000, max_value=50000))
def test_p26_oos_parameter_isolation(mutated_price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    train_c = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(20)]
    val_c = [
        _make_candle(t0 + timedelta(minutes=3 * (20 + i)), Decimal("24050")) for i in range(10)
    ]
    test_c = [
        _make_candle(t0 + timedelta(minutes=3 * (30 + i)), Decimal("24100")) for i in range(10)
    ]
    mutated_test_c = [
        _make_candle(t0 + timedelta(minutes=3 * (30 + i)), Decimal(mutated_price))
        for i in range(10)
    ]

    train_ds = BacktestDataset("TRAIN", train_c)
    val_ds = BacktestDataset("VAL", val_c)
    test_ds = BacktestDataset("TEST", test_c)
    mut_test_ds = BacktestDataset("MUT_TEST", mutated_test_c)

    clean = ParameterIsolationWorkflow.verify_oos_non_contamination(
        train_dataset=train_ds,
        validation_dataset=val_ds,
        test_dataset=test_ds,
        mutated_test_dataset=mut_test_ds,
        supplied_parameters={"param1": 100},
    )
    assert clean is True


# ---------------------------------------------------------------------------
# P27: Post-expiry execution rejection
# ---------------------------------------------------------------------------
def test_p27_post_expiry_execution_rejection() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    ds = BacktestDataset("DS_P27", candles)
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY-SPOT",
        expiry_datetime=t0,
        listing_datetime=t0 - timedelta(days=90),
        trading_start_datetime=t0 - timedelta(days=90),
        trading_end_datetime=t0,
        lot_size=50,
        tick_size=Decimal("0.05"),
        data_source="NSE",
    )
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, ds, contract_master=contract)
    res = engine.run()
    assert engine.post_expiry_fills == 0
    assert len(res.trades) == 0
    gate_e = next(g for g in res.quant_gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.PASS
    assert gate_e.evidence["post_expiry_fills"] == 0
    assert gate_e.evidence["contract_metadata_valid"] is True
    assert gate_e.evidence["lifecycle_violation"] is False


# ---------------------------------------------------------------------------
# P28: Disabled verification cannot silently pass
# ---------------------------------------------------------------------------
def test_p28_disabled_verification_cannot_silently_pass() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    ds = BacktestDataset("DS_P28", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    res = BacktestEngine(cfg, ds).run()
    gate_a = next(g for g in res.quant_gates if g.gate_id == "Gate A")
    gate_g = next(g for g in res.quant_gates if g.gate_id == "Gate G")
    assert gate_a.status == QuantGateStatus.WARNING
    assert gate_g.status == QuantGateStatus.WARNING
    assert res.validation_status != ValidationStatus.VALID


# ---------------------------------------------------------------------------
# P29: Configuration fingerprint changes run_id
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(extra_bars=st.integers(min_value=1, max_value=10))
def test_p29_config_fingerprint_changes_run_id(extra_bars: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    ds = BacktestDataset("DS_P29", [_make_candle(t0, Decimal("24000"))])
    base_cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
    )
    base_id = compute_backtest_run_id(base_cfg, ds.metadata)

    modified_cfg = base_cfg.model_copy(update={"warmup_bars": 15 + extra_bars})
    modified_id = compute_backtest_run_id(modified_cfg, ds.metadata)
    assert base_id != modified_id


# ---------------------------------------------------------------------------
# P30: Identical complete runs produce identical result hash
# ---------------------------------------------------------------------------
@settings(max_examples=5, deadline=None)
@given(price=st.integers(min_value=23000, max_value=25000))
def test_p30_identical_complete_runs_produce_identical_result_hash(price: int) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal(price)) for i in range(10)]
    ds = BacktestDataset("DS_P30", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        warmup_bars=5,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    res1 = BacktestEngine(cfg, ds).run()
    res2 = BacktestEngine(cfg, ds).run()
    assert res1.result_canonical_hash == res2.result_canonical_hash
    assert res1.trace_canonical_hash == res2.trace_canonical_hash


# ---------------------------------------------------------------------------
# P31: Normal contract expiry handling does not cause Gate E failure
# ---------------------------------------------------------------------------
@given(
    st.integers(min_value=1, max_value=8),
    st.integers(min_value=1, max_value=100),
)
@settings(max_examples=15, deadline=None)
def test_p31_normal_contract_expiry_does_not_cause_gate_e_failure(
    expiry_offset: int, lot_size: int
) -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_make_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    ds = BacktestDataset("DS_P31", candles)
    expiry_ts = t0 + timedelta(minutes=3 * expiry_offset)
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY-SPOT",
        expiry_datetime=expiry_ts,
        listing_datetime=t0 - timedelta(days=90),
        trading_start_datetime=t0 - timedelta(days=90),
        trading_end_datetime=expiry_ts,
        lot_size=lot_size,
        tick_size=Decimal("0.05"),
        data_source="NSE",
    )
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, ds, contract_master=contract)
    res = engine.run()
    assert engine.post_expiry_fills == 0
    gate_e = next(g for g in res.quant_gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.PASS
    assert gate_e.evidence["contract_metadata_valid"] is True
    assert gate_e.evidence["lifecycle_violation"] is False
    assert gate_e.evidence["post_expiry_fills"] == 0
