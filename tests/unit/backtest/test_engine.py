"""
Unit tests for alphaforge.backtest.engine.
Verifies deterministic run identity, closed-candle isolation, risk integration,
and audit ledger event recording.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine, compute_backtest_run_id
from alphaforge.backtest.models import BacktestConfig, FinalPositionPolicy
from alphaforge.contract.models import ContractMaster
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.models import RiskConfig
from alphaforge.strategy.config import StrategyConfig

FIXTURES_DIR = Path(__file__).parents[2] / "golden" / "fixtures"


def _load_fixture_candles(filename: str = "scenario_01_valid_long.json") -> list[MarketCandle]:
    with (FIXTURES_DIR / filename).open(encoding="utf-8") as f:
        data = json.load(f)
    return [
        MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            contract_id="NIFTY-SPOT",
            exchange_timestamp=datetime.fromisoformat(c["timestamp"]),
            received_timestamp=datetime.fromisoformat(c["timestamp"]) + timedelta(milliseconds=10),
            timeframe="3m",
            open=Decimal(c["open"]),
            high=Decimal(c["high"]),
            low=Decimal(c["low"]),
            close=Decimal(c["close"]),
            volume=int(c["volume"]),
            source="NSE",
        )
        for c in reversed(data["exec_candles"][1:])
    ]


def _create_candle(ts: datetime, close_p: Decimal, vol: int = 1000) -> MarketCandle:
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
        volume=vol,
        open_interest=50000,
        source="NSE",
    )


def test_compute_backtest_run_id_determinism() -> None:
    """Verify run_id format BT-RUN-[0-9A-F]{24} and canonical determinism."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=30)
    c1 = _create_candle(t0, Decimal("24000"))
    ds = BacktestDataset("NIFTY_DS", [c1])

    cfg1 = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t1,
        initial_capital=Decimal("1000000"),
    )

    id1 = compute_backtest_run_id(cfg1, ds.metadata)
    id2 = compute_backtest_run_id(cfg1, ds.metadata)

    assert id1 == id2
    assert id1.startswith("BT-RUN-")
    assert len(id1) == 7 + 24

    # Different capital -> different run_id
    cfg_cap = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t1,
        initial_capital=Decimal("2000000"),
    )
    assert compute_backtest_run_id(cfg_cap, ds.metadata) != id1

    # Different policy -> different run_id
    cfg_pol = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t1,
        initial_capital=Decimal("1000000"),
        final_position_policy=FinalPositionPolicy.FORCE_CLOSE,
    )
    assert compute_backtest_run_id(cfg_pol, ds.metadata) != id1


def test_engine_audit_ledger_events() -> None:
    """Verify engine logs execution events with correlation_id = backtest_run_id."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(10)]
    dataset = BacktestDataset("TEST_RUN_DS", candles)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(minutes=30),
        initial_capital=Decimal("1000000"),
    )

    ledger = AuditLedger(storage=InMemoryLedgerStorage())
    engine = BacktestEngine(config=cfg, dataset=dataset, ledger=ledger)
    result = engine.run()

    assert result.backtest_run_id == engine.run_id
    assert result.completion_status == "COMPLETED"

    # Verify audit events
    events = ledger.get_events_by_correlation_id(engine.run_id)
    assert len(events) >= 2  # BACKTEST_STARTED and BACKTEST_COMPLETED
    assert events[0].event_type == AuditEventType.BACKTEST_STARTED
    assert events[-1].event_type == AuditEventType.BACKTEST_COMPLETED
    assert all(e.correlation_id == engine.run_id for e in events)

    # Verify cryptographic chain integrity
    assert ledger.verify_chain().valid is True


def test_material_fingerprints_change_run_id() -> None:
    """Verify that every material component fingerprint uniquely alters the run_id."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)
    c = _create_candle(t0, Decimal("24000"))
    ds = BacktestDataset("DS_FINGERPRINT", [c])

    base_cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=ds.dataset_id,
        start_time=t0,
        end_time=t1,
        initial_capital=Decimal("1000000"),
    )
    base_id = compute_backtest_run_id(base_cfg, ds.metadata)

    # 1. StrategyConfig fingerprint sensitivity
    strat_cfg_diff = StrategyConfig(atr_period=25)
    id_strat = compute_backtest_run_id(base_cfg, ds.metadata, strategy_config=strat_cfg_diff)
    assert id_strat != base_id

    # 2. RiskConfig fingerprint sensitivity
    risk_cfg_diff = RiskConfig(max_open_trades=10)
    id_risk = compute_backtest_run_id(base_cfg, ds.metadata, risk_config=risk_cfg_diff)
    assert id_risk != base_id

    # 3. ContractMaster fingerprint sensitivity
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
    id_contract = compute_backtest_run_id(base_cfg, ds.metadata, contract_master=contract)
    assert id_contract != base_id

    # 4. CostConfig fingerprint sensitivity
    cost_cfg_diff = base_cfg.model_copy(
        update={"cost_config": CostConfig(entry_fee_rate=Decimal("0.002"))}
    )
    id_cost = compute_backtest_run_id(cost_cfg_diff, ds.metadata)
    assert id_cost != base_id

    # 5. Engine Version fingerprint sensitivity
    engine_ver_diff = base_cfg.model_copy(update={"engine_version": "PHASE10_BACKTEST_V2"})
    id_ver = compute_backtest_run_id(engine_ver_diff, ds.metadata)
    assert id_ver != base_id


def test_risk_engine_divergence() -> None:
    """Verify that permissive vs restrictive RiskConfig causes proven outcome divergence."""
    candles = _load_fixture_candles()
    last_c = candles[-1]
    next_c = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=last_c.exchange_timestamp + timedelta(minutes=3),
        received_timestamp=last_c.exchange_timestamp + timedelta(minutes=3, milliseconds=10),
        timeframe="3m",
        open=Decimal("24100.00"),
        high=Decimal("24150.00"),
        low=Decimal("24090.00"),
        close=Decimal("24120.00"),
        volume=2000,
        source="NSE",
    )
    candles.append(next_c)

    dataset = BacktestDataset("DIVERGENCE_DS", candles)
    t_start = candles[0].exchange_timestamp
    t_end = candles[-1].exchange_timestamp + timedelta(minutes=3)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t_start,
        end_time=t_end,
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
        final_position_policy=FinalPositionPolicy.FORCE_CLOSE,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )

    # Permissive run
    permissive_risk = RiskConfig()
    eng_perm = BacktestEngine(config=cfg, dataset=dataset, risk_config=permissive_risk)
    res_perm = eng_perm.run()

    # Restrictive run: tiny position notional limit forces risk engine rejection
    restrictive_risk = RiskConfig(max_single_position_notional=Decimal("0.0001"))
    eng_rest = BacktestEngine(config=cfg, dataset=dataset, risk_config=restrictive_risk)
    res_rest = eng_rest.run()

    # Outcome divergence proof
    assert eng_perm.risk_evaluations_count == 1
    assert eng_perm.risk_approvals_count == 1
    assert eng_perm.risk_rejections_recorded == 0
    assert len(res_perm.trades) == 1

    assert eng_rest.risk_evaluations_count == 1
    assert eng_rest.risk_rejections_recorded == 1
    assert eng_rest.risk_approvals_count == 0
    assert len(res_rest.trades) == 0


def test_closed_candle_proof_bar_t_close_ne_bar_t_plus_1_open() -> None:
    """
    Closed-Candle Proof:
    Verify that a signal generated on bar T close executes at bar T+1 open,
    and when bar T close != bar T+1 open (e.g. gap), the execution entry price
    strictly reflects bar T+1 open (plus slippage), NEVER bar T close.
    """
    candles = _load_fixture_candles()
    bar_t = candles[-1]
    # Introduce deliberate gap open on bar T+1
    gap_open = bar_t.close + Decimal("75.50")
    bar_t_plus_1 = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=bar_t.exchange_timestamp + timedelta(minutes=3),
        received_timestamp=bar_t.exchange_timestamp + timedelta(minutes=3, milliseconds=10),
        timeframe="3m",
        open=gap_open,
        high=gap_open + Decimal("20.00"),
        low=gap_open - Decimal("10.00"),
        close=gap_open + Decimal("5.00"),
        volume=2500,
        source="NSE",
    )
    candles.append(bar_t_plus_1)

    dataset = BacktestDataset("GAP_DS", candles)
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=candles[0].exchange_timestamp,
        end_time=candles[-1].exchange_timestamp + timedelta(minutes=3),
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
        final_position_policy=FinalPositionPolicy.FORCE_CLOSE,
        cost_config=CostConfig(
            entry_fee_rate=Decimal("0"),
            exit_fee_rate=Decimal("0"),
            entry_slippage_rate=Decimal("0"),
            exit_slippage_rate=Decimal("0"),
        ),
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(config=cfg, dataset=dataset)
    res = engine.run()

    # Trade entered on bar T+1 open
    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.entry_timestamp == bar_t_plus_1.exchange_timestamp
    # Entry price must match bar T+1 open, NOT bar T close
    assert trade.entry_price == bar_t_plus_1.open
    assert trade.entry_price != bar_t.close
    assert trade.entry_price == gap_open
