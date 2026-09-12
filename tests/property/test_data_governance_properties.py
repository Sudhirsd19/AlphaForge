"""
Property-based tests for AlphaForge Market Data Layer using Hypothesis.
Verifies invariants: permutation invariance, duplicate idempotence, and price bounds.
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.normalization import MarketDataNormalizer


def _generate_valid_candle(step_idx: int, price_offset: int) -> MarketCandle:
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    ts = base_t + timedelta(minutes=3 * step_idx)
    open_p = Decimal("25000") + Decimal(price_offset)
    high_p = open_p + Decimal("25.00")
    low_p = open_p - Decimal("25.00")
    close_p = open_p + Decimal("10.00")

    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000 + step_idx * 10,
        source="HYPOTHESIS_TEST",
    )


@given(
    n_candles=st.integers(min_value=2, max_value=15),
    shuffle_seed=st.integers(min_value=0, max_value=10000),
)
@settings(max_examples=30, deadline=None)
def test_property_permutation_invariance(n_candles: int, shuffle_seed: int) -> None:
    """Any arbitrary permutation of input records produces bit-exact identical valid candles."""
    normalizer = MarketDataNormalizer()
    candles = [_generate_valid_candle(i, i * 5) for i in range(n_candles)]

    shuffled = list(candles)
    rng = random.Random(shuffle_seed)  # noqa: S311
    rng.shuffle(shuffled)

    eval_ts = candles[-1].exchange_timestamp + timedelta(seconds=10)
    res_ordered = normalizer.normalize_batch(candles, evaluation_timestamp=eval_ts)
    res_shuffled = normalizer.normalize_batch(shuffled, evaluation_timestamp=eval_ts)

    assert len(res_ordered.valid_candles) == len(res_shuffled.valid_candles)
    for c1, c2 in zip(res_ordered.valid_candles, res_shuffled.valid_candles, strict=True):
        assert c1.exchange_timestamp == c2.exchange_timestamp
        assert c1.open == c2.open
        assert c1.high == c2.high
        assert c1.low == c2.low
        assert c1.close == c2.close
        assert c1.volume == c2.volume


@given(
    n_candles=st.integers(min_value=2, max_value=10),
    num_duplicates=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=25, deadline=None)
def test_property_duplicate_idempotence(n_candles: int, num_duplicates: int) -> None:
    """Adding duplicate copies collapses cleanly without altering clean series content."""
    normalizer = MarketDataNormalizer()
    candles = [_generate_valid_candle(i, i * 5) for i in range(n_candles)]

    # Pick a subset to duplicate
    actual_num_duplicates = min(num_duplicates, n_candles)
    extra_duplicates = candles[:actual_num_duplicates]
    batch_with_dupes = candles + extra_duplicates

    eval_ts = candles[-1].exchange_timestamp + timedelta(seconds=10)
    res = normalizer.normalize_batch(batch_with_dupes, evaluation_timestamp=eval_ts)

    assert len(res.valid_candles) == n_candles
    assert res.duplicates_count == actual_num_duplicates

    # Strict monotonicity check
    for i in range(len(res.valid_candles) - 1):
        assert res.valid_candles[i].exchange_timestamp < res.valid_candles[i + 1].exchange_timestamp
