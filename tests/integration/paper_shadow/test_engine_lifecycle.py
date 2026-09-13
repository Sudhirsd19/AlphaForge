"""
Integration tests for AlphaForge Paper / Shadow Engine Lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import ForwardRunState, PaperShadowMode
from alphaforge.paper_shadow.models import PaperShadowConfig


def generate_candle_series(n: int = 30) -> list[MarketCandle]:
    base_time = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    candles = []
    base_price = Decimal("20000")
    for i in range(n):
        t = base_time + timedelta(minutes=3 * i)
        # Steady uptrend to provide valid breakout signals
        p = base_price + Decimal(i * 10)
        c = MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.FUTURES,
            contract_id="NIFTY-FUT",
            exchange_timestamp=t,
            received_timestamp=t + timedelta(milliseconds=20),
            timeframe="3m",
            open=p,
            high=p + Decimal("20"),
            low=p - Decimal("5"),
            close=p + Decimal("15"),
            volume=5000 + i * 100,
            source="TEST_FEED",
            quality_status=DataQualityStatus.VALID,
            is_closed=True,
        )
        candles.append(c)
    return candles


def test_paper_engine_lifecycle_and_shutdown() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    engine = PaperShadowEngine(config=cfg)
    assert engine.state == ForwardRunState.RUNNING

    candles = generate_candle_series(25)
    for c in candles:
        res = engine.process_candle(c)
        assert res.is_valid is True

    report = engine.shutdown()
    assert engine.state == ForwardRunState.STOPPED
    assert report.total_candles_processed == 25
    assert report.no_live_orders_submitted is True
    assert report.mode == PaperShadowMode.PAPER


def test_shadow_engine_lifecycle_zero_orders() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.SHADOW)
    engine = PaperShadowEngine(config=cfg)

    candles = generate_candle_series(25)
    for c in candles:
        engine.process_candle(c)

    report = engine.shutdown()
    assert report.total_orders_submitted == 0
    assert report.mode == PaperShadowMode.SHADOW
    assert report.no_live_orders_submitted is True
    assert len(engine.shadow_comparator.get_observations()) == 25


def test_kill_switch_blocks_execution() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    engine = PaperShadowEngine(config=cfg)

    # Engage kill switch
    engine.kill_switch.engage(reason="Test Emergency")

    candles = generate_candle_series(10)
    for c in candles:
        engine.process_candle(c)

    report = engine.shutdown()
    # Zero orders should have been submitted due to kill switch
    assert report.total_orders_submitted == 0
