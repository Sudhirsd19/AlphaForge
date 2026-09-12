"""
Unit tests for duplicate deduplication and conflict quarantine arbitration.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.normalization import MarketDataNormalizer


def _create_candle(
    ts: datetime,
    close_p: Decimal = Decimal("25000.00"),
    volume: int = 1000,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe="3m",
        open=close_p,
        high=close_p + Decimal("20.00"),
        low=close_p - Decimal("20.00"),
        close=close_p,
        volume=volume,
        source="TEST",
    )


def test_identical_duplicate_deduplication() -> None:
    """Identical duplicate records are safely and idempotently collapsed."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    c0_a = _create_candle(t0, Decimal("25000.00"), volume=1000)
    c0_b = _create_candle(t0, Decimal("25000.00"), volume=1000)

    res = normalizer.normalize_batch([c0_a, c0_b], evaluation_timestamp=t0 + timedelta(seconds=10))

    assert len(res.valid_candles) == 1
    assert res.duplicates_count == 1
    assert len(res.quarantined_records) == 0
    assert res.quality_status == DataQualityStatus.DUPLICATE


def test_conflicting_records_quarantine() -> None:
    """
    Conflicting records sharing identical key (symbol, timeframe, timestamp)
    with differing OHLCV must both be quarantined. Neither is ingested into valid_candles.
    """
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)

    # Valid candle at t0
    c0 = _create_candle(t0, Decimal("25000.00"))

    # Conflicting candles at t1: different close prices
    c1_venue_a = _create_candle(t1, Decimal("25050.00"))
    c1_venue_b = _create_candle(t1, Decimal("25030.00"))

    res = normalizer.normalize_batch(
        [c0, c1_venue_a, c1_venue_b], evaluation_timestamp=t1 + timedelta(seconds=10)
    )

    # Only c0 survives into valid_candles; both conflicting records at t1 are quarantined
    assert len(res.valid_candles) == 1
    assert res.valid_candles[0].exchange_timestamp == t0
    assert len(res.quarantined_records) == 2
    assert all(q.status == DataQualityStatus.CONFLICT for q in res.quarantined_records)
    assert res.quality_status == DataQualityStatus.CONFLICT


def test_conflict_takes_precedence_over_duplicate() -> None:
    """Precedence hierarchy: CONFLICT (Rank 2) > DUPLICATE (Rank 4)."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)

    # Duplicate at t0
    c0_a = _create_candle(t0, Decimal("25000.00"))
    c0_b = _create_candle(t0, Decimal("25000.00"))

    # Conflict at t1
    c1_a = _create_candle(t1, Decimal("25050.00"))
    c1_b = _create_candle(t1, Decimal("25020.00"))

    res = normalizer.normalize_batch(
        [c0_a, c0_b, c1_a, c1_b], evaluation_timestamp=t1 + timedelta(seconds=10)
    )

    assert res.duplicates_count == 1
    assert len(res.quarantined_records) == 2
    assert res.quality_status == DataQualityStatus.CONFLICT
