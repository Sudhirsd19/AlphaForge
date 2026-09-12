"""
Unit tests for alphaforge.backtest.reports.
Verifies Markdown and text report generation and section separation.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig
from alphaforge.backtest.reports import (
    generate_backtest_markdown_report,
    generate_backtest_text_report,
)
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle


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
        volume=1000,
        source="NSE",
    )


def test_report_generation() -> None:
    """Verify structured report contains all mandatory sections."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS_REPORT", [_create_candle(t0, Decimal("24000"))])

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t0,
        end_time=t0 + timedelta(hours=1),
        initial_capital=Decimal("1000000"),
    )

    engine = BacktestEngine(config=cfg, dataset=dataset)
    result = engine.run()

    md_report = generate_backtest_markdown_report(result)
    assert "ALPHAFORGE QUANTITATIVE VALIDATION REPORT" in md_report
    assert "1. Reproducibility & Provenance Metadata" in md_report
    assert "2. Financial Performance (Gross vs Friction vs Net)" in md_report
    assert "3. Trade Execution Statistics" in md_report
    assert "4. Risk & Drawdown Metrics" in md_report
    assert "5. Quant Gates A through J Audit" in md_report
    assert "6. Overfitting Diagnostics & Telemetry" in md_report

    text_report = generate_backtest_text_report(result)
    assert len(text_report) > 100
