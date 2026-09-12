"""
Unit tests for MarketDataNormalizer ordering, freshness, and precedence.
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.normalization import MarketDataNormalizer


def _create_candle(ts: datetime, close_p: Decimal = Decimal("25000.00")) -> MarketCandle:
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
        volume=1000,
        source="TEST",
    )


def test_normalizer_empty_batch() -> None:
    """Empty batch returns EMPTY status and zero candles."""
    normalizer = MarketDataNormalizer()
    res = normalizer.normalize_batch([])
    assert res.quality_status == DataQualityStatus.EMPTY
    assert len(res.valid_candles) == 0


def test_normalizer_chronological_sorting() -> None:
    """Out of order records are sorted strictly chronologically."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)
    t2 = datetime(2026, 9, 11, 9, 21, tzinfo=UTC)

    c0 = _create_candle(t0, Decimal("25000.00"))
    c1 = _create_candle(t1, Decimal("25010.00"))
    c2 = _create_candle(t2, Decimal("25020.00"))

    # Pass in reversed order [c2, c1, c0]
    res = normalizer.normalize_batch([c2, c1, c0], evaluation_timestamp=t2 + timedelta(seconds=10))

    assert res.was_out_of_order is True
    assert len(res.valid_candles) == 3
    assert res.valid_candles[0].exchange_timestamp == t0
    assert res.valid_candles[1].exchange_timestamp == t1
    assert res.valid_candles[2].exchange_timestamp == t2
    # OUT_OF_ORDER has higher precedence than VALID
    assert res.quality_status == DataQualityStatus.OUT_OF_ORDER


def test_normalizer_permutation_invariance() -> None:
    """Shuffled batches yield the exact same ordered output."""
    normalizer = MarketDataNormalizer()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    candles = [
        _create_candle(base_t + timedelta(minutes=3 * i), Decimal(25000 + i * 5)) for i in range(10)
    ]

    shuffled = list(candles)
    random.seed(42)
    random.shuffle(shuffled)

    eval_ts = base_t + timedelta(minutes=3 * 9, seconds=10)
    res_ordered = normalizer.normalize_batch(candles, evaluation_timestamp=eval_ts)
    res_shuffled = normalizer.normalize_batch(shuffled, evaluation_timestamp=eval_ts)

    assert len(res_ordered.valid_candles) == len(res_shuffled.valid_candles)
    for c1, c2 in zip(res_ordered.valid_candles, res_shuffled.valid_candles, strict=True):
        assert c1.exchange_timestamp == c2.exchange_timestamp
        assert c1.close == c2.close


def test_normalizer_stale_detection() -> None:
    """If latest candle is older than max_stale_seconds, batch is flagged STALE."""
    normalizer = MarketDataNormalizer(max_stale_seconds=195)
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    c0 = _create_candle(t0)

    # Evaluation timestamp is 200s after t0 (> 195s)
    eval_ts = t0 + timedelta(seconds=200)
    res = normalizer.normalize_batch([c0], evaluation_timestamp=eval_ts)

    assert res.quality_status == DataQualityStatus.STALE


def test_normalizer_incomplete_detection() -> None:
    """If candle count is below min_required_candles, batch is flagged INCOMPLETE."""
    normalizer = MarketDataNormalizer(min_required_candles=5)
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    candles = [
        _create_candle(t0 + timedelta(minutes=3 * i)) for i in range(3)
    ]  # only 3 candles < 5

    eval_ts = t0 + timedelta(minutes=6, seconds=10)
    res = normalizer.normalize_batch(candles, evaluation_timestamp=eval_ts)

    assert res.quality_status == DataQualityStatus.INCOMPLETE
