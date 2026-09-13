"""
Integration tests for AlphaForge Forward Validation Runner.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.forward_runner import ForwardValidationRunner
from alphaforge.paper_shadow.models import PaperShadowConfig


def generate_candle_series(n: int = 30) -> list[MarketCandle]:
    base_time = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    candles = []
    base_price = Decimal("20000")
    for i in range(n):
        t = base_time + timedelta(minutes=3 * i)
        p = base_price + Decimal(i * 15)
        c = MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.FUTURES,
            contract_id="NIFTY-FUT",
            exchange_timestamp=t,
            received_timestamp=t + timedelta(milliseconds=20),
            timeframe="3m",
            open=p,
            high=p + Decimal("25"),
            low=p - Decimal("5"),
            close=p + Decimal("20"),
            volume=6000 + i * 50,
            source="TEST_FEED",
            quality_status=DataQualityStatus.VALID,
            is_closed=True,
        )
        candles.append(c)
    return candles


def test_forward_runner_batch_execution() -> None:
    candles = generate_candle_series(30)
    runner = ForwardValidationRunner()
    report = runner.run_stream(candles)

    assert report.total_candles_processed == 30
    assert report.no_live_orders_submitted is True
    assert report.mode == PaperShadowMode.PAPER


def test_forward_runner_determinism_proof() -> None:
    candles = generate_candle_series(30)

    # Run 1
    cfg1 = PaperShadowConfig()
    runner1 = ForwardValidationRunner(config=cfg1)
    rep1 = runner1.run_stream(candles)

    # Run 2
    cfg2 = PaperShadowConfig()
    runner2 = ForwardValidationRunner(config=cfg2)
    rep2 = runner2.run_stream(candles)

    assert rep1.total_candles_processed == rep2.total_candles_processed
    assert rep1.total_signals_generated == rep2.total_signals_generated
    assert rep1.total_orders_submitted == rep2.total_orders_submitted
    assert rep1.total_fills_executed == rep2.total_fills_executed
    assert rep1.metrics.gross_pnl == rep2.metrics.gross_pnl
    assert rep1.metrics.net_pnl == rep2.metrics.net_pnl
    assert rep1.metrics.total_fees == rep2.metrics.total_fees
    assert rep1.metrics.total_slippage == rep2.metrics.total_slippage
    assert rep1.configuration_hash == rep2.configuration_hash
