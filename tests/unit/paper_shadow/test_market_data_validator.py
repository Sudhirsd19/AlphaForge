"""
Unit tests for AlphaForge Market Data Validator in Paper / Shadow Trading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.enums import MarketDataAnomalyType
from alphaforge.paper_shadow.market_data_validator import (
    PaperMarketDataValidator,
)
from alphaforge.paper_shadow.models import MarketEvent, PaperShadowConfig


def make_candle(
    symbol: str = "NIFTY",
    ts: datetime | None = None,
    timeframe: str = "3m",
    open_p: Decimal = Decimal("20000"),
    high_p: Decimal = Decimal("20100"),
    low_p: Decimal = Decimal("19950"),
    close_p: Decimal = Decimal("20050"),
    is_closed: bool = True,
    volume: int = 1000,
) -> MarketCandle:
    t = ts or datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol=symbol,
        instrument_type=InstrumentType.FUTURES,
        contract_id=f"{symbol}-FUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=50),
        timeframe=timeframe,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=is_closed,
    )


def test_validator_accepts_valid_candle() -> None:
    validator = PaperMarketDataValidator()
    c1 = make_candle(ts=datetime(2026, 9, 12, 9, 15, tzinfo=UTC))
    res = validator.validate_candle(c1)
    assert res.is_valid is True
    assert res.anomaly is None


def test_validator_quarantines_forming_bars() -> None:
    validator = PaperMarketDataValidator()
    c = make_candle(is_closed=False)
    res = validator.validate_candle(c)
    assert res.is_valid is False
    assert res.anomaly == MarketDataAnomalyType.FORMING_BAR_LEAK


def test_validator_rejects_out_of_order_timestamps() -> None:
    validator = PaperMarketDataValidator()
    c1 = make_candle(ts=datetime(2026, 9, 12, 9, 18, tzinfo=UTC))
    c2 = make_candle(ts=datetime(2026, 9, 12, 9, 15, tzinfo=UTC))

    res1 = validator.validate_candle(c1)
    assert res1.is_valid is True

    res2 = validator.validate_candle(c2)
    assert res2.is_valid is False
    assert res2.anomaly == MarketDataAnomalyType.TIMESTAMP_OUT_OF_ORDER


def test_validator_rejects_duplicate_timestamps() -> None:
    validator = PaperMarketDataValidator()
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    c1 = make_candle(ts=t)
    c2 = make_candle(ts=t)

    assert validator.validate_candle(c1).is_valid is True
    res2 = validator.validate_candle(c2)
    assert res2.is_valid is False
    assert res2.anomaly == MarketDataAnomalyType.DUPLICATE_CANDLE


def test_validator_timeframe_aware_stale_data() -> None:
    config = PaperShadowConfig(stale_data_threshold_seconds=15)
    validator = PaperMarketDataValidator(config)

    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    # 15m candle (15m = 900s) + 15s threshold = 915s allowed lag
    c_15m = make_candle(ts=t, timeframe="15m")

    # 100s lag: should NOT be marked stale because 100s < 915s
    res1 = validator.validate_candle(c_15m, receipt_timestamp=t + timedelta(seconds=100))
    assert res1.is_valid is True

    # 1000s lag: should be marked stale because 1000s > 915s
    validator.reset()
    res2 = validator.validate_candle(c_15m, receipt_timestamp=t + timedelta(seconds=1000))
    assert res2.is_valid is False
    assert res2.anomaly == MarketDataAnomalyType.STALE_DATA


def test_validator_event_deduplication() -> None:
    validator = PaperMarketDataValidator()
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    c = make_candle(ts=t)
    evt1 = MarketEvent(
        event_id="EVT-01",
        sequence=1,
        candle=c,
        market_timestamp=t,
        receipt_timestamp=t + timedelta(milliseconds=10),
        symbol="NIFTY",
    )
    evt2 = MarketEvent(
        event_id="EVT-01",
        sequence=2,
        candle=make_candle(ts=t + timedelta(minutes=3)),
        market_timestamp=t + timedelta(minutes=3),
        receipt_timestamp=t + timedelta(minutes=3, milliseconds=10),
        symbol="NIFTY",
    )

    res1 = validator.validate_event(evt1)
    assert res1.is_valid is True

    res2 = validator.validate_event(evt2)
    assert res2.is_valid is False
    assert res2.anomaly == MarketDataAnomalyType.DUPLICATE_CANDLE
