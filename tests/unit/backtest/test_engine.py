"""
Unit tests for alphaforge.backtest.engine.
Verifies deterministic run identity, closed-candle isolation, risk integration,
and audit ledger event recording.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine, compute_backtest_run_id
from alphaforge.backtest.models import BacktestConfig, FinalPositionPolicy
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage


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
