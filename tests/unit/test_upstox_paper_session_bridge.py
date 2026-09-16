from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts.run_upstox_paper_session import _TimeframeAggregator


def _event(minute: int, price: str, volume: int = 10) -> SimpleNamespace:
    ts = datetime(2026, 9, 16, 3, minute, tzinfo=UTC)
    value = Decimal(price)
    return SimpleNamespace(
        exchange_timestamp=ts,
        open_price=value,
        high_price=value + Decimal("1"),
        low_price=value - Decimal("1"),
        close_price=value,
        volume=volume,
    )


def test_three_minute_aggregator_is_causal_and_exact() -> None:
    agg = _TimeframeAggregator(3)

    assert agg.add(_event(0, "100")) is None
    assert agg.add(_event(1, "101", 20)) is None
    assert agg.add(_event(2, "102", 30)) is None

    closed = agg.add(_event(3, "103", 40))
    assert closed is not None
    assert closed.start == datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
    assert closed.open == Decimal("100")
    assert closed.high == Decimal("103")
    assert closed.low == Decimal("99")
    assert closed.close == Decimal("102")
    assert closed.volume == 60


def test_fifteen_minute_aggregator_emits_only_after_bucket_changes() -> None:
    agg = _TimeframeAggregator(15)

    for minute in range(15):
        assert agg.add(_event(minute, str(100 + minute))) is None

    closed = agg.add(_event(15, "115"))
    assert closed is not None
    assert closed.start == datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
    assert closed.close == Decimal("114")


def test_aggregator_rejects_out_of_order_buckets() -> None:
    agg = _TimeframeAggregator(3)
    agg.add(_event(3, "103"))

    with pytest.raises(ValueError, match="Out-of-order bar"):
        agg.add(_event(0, "100"))


def test_shutdown_does_not_evaluate_partial_bucket() -> None:
    agg = _TimeframeAggregator(3)
    agg.add(_event(0, "100"))
    agg.add(_event(1, "101"))
    agg.discard_open_bucket()

    # A new bucket can start cleanly after a discard; the discarded partial
    # bucket can never be emitted as an evaluated candle.
    closed = agg.add(_event(3, "103"))
    assert closed is None


def test_event_spacing_is_minute_aligned() -> None:
    first = _event(0, "100").exchange_timestamp
    fourth = _event(3, "103").exchange_timestamp
    assert fourth - first == timedelta(minutes=3)
