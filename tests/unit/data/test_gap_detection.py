"""
Unit tests for gap detection and zero-imputation policy.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.normalization import MarketDataNormalizer
from alphaforge.data.timeframe import has_candle_gap


def _create_candle(
    ts: datetime,
    timeframe: str = "3m",
    close_p: Decimal = Decimal("25000.00"),
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe=timeframe,
        open=close_p,
        high=close_p + Decimal("20.00"),
        low=close_p - Decimal("20.00"),
        close=close_p,
        volume=1000,
        source="TEST",
    )


def test_has_candle_gap_helper() -> None:
    """Verify has_candle_gap correctly identifies intervals exceeding timeframe delta."""
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)
    t2 = datetime(2026, 9, 11, 9, 21, tzinfo=UTC)
    t_gap = datetime(2026, 9, 11, 9, 24, tzinfo=UTC)

    # Consecutive 3m candles: no gap
    assert has_candle_gap(t0, t1, "3m") is False
    assert has_candle_gap(t1, t2, "3m") is False

    # Skipped 09:21, jumping directly from 09:18 to 09:24: gap!
    assert has_candle_gap(t1, t_gap, "3m") is True


def test_gap_detection_3m() -> None:
    """Missing 3m candle triggers GAP status without hallucinating synthetic data."""
    normalizer = MarketDataNormalizer(default_timeframe="3m")
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)
    # Skipping 09:21!
    t3 = datetime(2026, 9, 11, 9, 24, tzinfo=UTC)

    c0 = _create_candle(t0, "3m")
    c1 = _create_candle(t1, "3m")
    c3 = _create_candle(t3, "3m")

    res = normalizer.normalize_batch([c0, c1, c3], evaluation_timestamp=t3 + timedelta(seconds=10))

    # Zero imputation: exactly the 3 supplied candles exist in valid_candles
    assert len(res.valid_candles) == 3
    assert len(res.detected_gaps) == 1
    assert res.detected_gaps[0] == (t1, t3)
    assert res.quality_status == DataQualityStatus.GAP


def test_gap_detection_15m() -> None:
    """Missing 15m candle triggers GAP status on 15m timeframe."""
    normalizer = MarketDataNormalizer(default_timeframe="15m")
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    # Skipping 09:30!
    t2 = datetime(2026, 9, 11, 9, 45, tzinfo=UTC)

    c0 = _create_candle(t0, "15m")
    c2 = _create_candle(t2, "15m")

    res = normalizer.normalize_batch(
        [c0, c2], timeframe="15m", evaluation_timestamp=t2 + timedelta(seconds=10)
    )

    assert len(res.valid_candles) == 2
    assert len(res.detected_gaps) == 1
    assert res.detected_gaps[0] == (t0, t2)
    assert res.quality_status == DataQualityStatus.GAP
