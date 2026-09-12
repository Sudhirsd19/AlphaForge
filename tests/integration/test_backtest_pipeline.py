"""
Integration tests for AlphaForge Backtest Pipeline.
Verifies complete end-to-end backtest lifecycle, cryptographic audit ledger integrity,
and forensic quant report generation.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig, FinalPositionPolicy, ValidationStatus
from alphaforge.backtest.reports import generate_backtest_markdown_report
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage


def _generate_synthetic_candles(n: int) -> list[MarketCandle]:
    candles = []
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    price = Decimal("24000")
    for i in range(n):
        ts = t0 + timedelta(minutes=3 * i)
        # Gentle trend
        price += Decimal("5") if i % 2 == 0 else Decimal("-2")
        c = MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.INDEX,
            contract_id="NIFTY-SPOT",
            exchange_timestamp=ts,
            received_timestamp=ts + timedelta(milliseconds=10),
            timeframe="3m",
            open=price,
            high=price + Decimal("8"),
            low=price - Decimal("8"),
            close=price + Decimal("2"),
            volume=5000 + i * 100,
            open_interest=50000,
            source="NSE",
        )
        candles.append(c)
    return candles


def test_full_backtest_pipeline_and_ledger_integrity() -> None:
    """
    Execute full backtest pipeline from raw candles to final report.
    Verify:
    1. Completion status is COMPLETED.
    2. AuditLedger forms a continuous cryptographic chain.
    3. Ledger events carry correlation_id == backtest_run_id.
    4. Markdown report contains all required forensic sections.
    """
    candles = _generate_synthetic_candles(60)
    dataset = BacktestDataset("NIFTY_SYNTH_60", candles)

    t_start = candles[0].exchange_timestamp
    t_end = candles[-1].exchange_timestamp + timedelta(minutes=3)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t_start,
        end_time=t_end,
        initial_capital=Decimal("1000000"),
        cost_config=CostConfig(
            entry_fee_rate=Decimal("0.0005"),
            exit_fee_rate=Decimal("0.0005"),
            entry_slippage_rate=Decimal("0.0002"),
            exit_slippage_rate=Decimal("0.0002"),
        ),
        final_position_policy=FinalPositionPolicy.FORCE_CLOSE,
        warmup_bars=20,
    )

    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage=storage)
    engine = BacktestEngine(config=cfg, dataset=dataset, ledger=ledger)

    result = engine.run()

    # 1. Pipeline result checks
    assert result.completion_status == "COMPLETED"
    assert result.validation_status in (
        ValidationStatus.VALID,
        ValidationStatus.WARNING,
        ValidationStatus.INSUFFICIENT_DATA,
    )
    assert len(result.equity_curve) == len(candles)

    # 2. Audit Ledger chain verification
    assert ledger.verify_chain().valid is True
    events = ledger.get_events_by_correlation_id(result.backtest_run_id)
    assert len(events) >= 2
    assert events[0].event_type == AuditEventType.BACKTEST_STARTED
    assert events[-1].event_type == AuditEventType.BACKTEST_COMPLETED
    assert all(e.correlation_id == result.backtest_run_id for e in events)

    # 3. Report generation
    report = generate_backtest_markdown_report(result)
    assert result.backtest_run_id in report
    assert "Quant Gates A through J Audit" in report
    assert "Financial Performance (Gross vs Friction vs Net)" in report
