"""
Phase 12 — Data Failure Tests (D1–D6).
Proves AlphaForge rejects corrupted, duplicate, out-of-order, and malformed market data.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.store import CandleStore
from alphaforge.data.validation import (
    parse_raw_record,
    validate_ohlc_boundaries,
    validate_temporal_causality,
    validate_volume,
)


def _make_candle(
    ts: datetime,
    open_p: str = "24000",
    high_p: str = "24010",
    low_p: str = "23990",
    close_p: str = "24005",
    volume: int = 1000,
    is_closed: bool = True,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY-2026-09",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe="3m",
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=volume,
        source="TEST",
        data_version=1,
        is_closed=is_closed,
    )


class TestD1MissingCandle:
    """D1: Missing candle causes deterministic failure or controlled gap handling."""

    def test_gap_in_candle_series_is_detectable(self) -> None:
        store = CandleStore()
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        # Add candles 1, 2, skip 3, add 4
        store.add_candle(_make_candle(base))
        store.add_candle(_make_candle(base + timedelta(minutes=3)))
        # Skip candle at base + 6min
        store.add_candle(_make_candle(base + timedelta(minutes=9)))

        candles = store.get_candles("NIFTY", "3m")
        # Gap is detectable: 3 candles present with a time gap
        assert len(candles) == 3
        # Verify timestamps show the gap
        timestamps = [c.exchange_timestamp for c in candles]
        gaps = [
            (timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)
        ]
        # One gap should be 6 minutes (double the interval)
        assert any(g > 180 for g in gaps), "Missing candle gap not detectable"

    def test_no_fabricated_candle_on_gap(self) -> None:
        store = CandleStore()
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        store.add_candle(_make_candle(base))
        store.add_candle(_make_candle(base + timedelta(minutes=6)))
        candles = store.get_candles("NIFTY", "3m")
        # System must NOT fabricate a candle to fill the gap
        assert len(candles) == 2


class TestD2DuplicateCandle:
    """D2: Duplicate candle is deterministically handled."""

    def test_identical_duplicate_is_idempotent(self) -> None:
        store = CandleStore()
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        candle = _make_candle(ts)
        store.add_candle(candle)
        store.add_candle(candle)  # Identical duplicate
        assert len(store.get_candles("NIFTY", "3m")) == 1

    def test_conflicting_duplicate_rejected(self) -> None:
        store = CandleStore()
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        store.add_candle(_make_candle(ts, close_p="24005"))
        with pytest.raises(DataIntegrityError, match="Conflicting"):
            store.add_candle(_make_candle(ts, close_p="24010"))


class TestD3OutOfOrderCandle:
    """D3: Out-of-order candles are handled deterministically."""

    def test_out_of_order_insertion_maintains_chronological_order(
        self,
    ) -> None:
        store = CandleStore()
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        c3 = _make_candle(base + timedelta(minutes=6))
        c1 = _make_candle(base)
        c2 = _make_candle(base + timedelta(minutes=3))

        # Insert out of order
        store.add_candle(c3)
        store.add_candle(c1)
        store.add_candle(c2)

        candles = store.get_candles("NIFTY", "3m")
        # Must be in chronological order regardless of insertion order
        for i in range(len(candles) - 1):
            assert candles[i].exchange_timestamp <= candles[i + 1].exchange_timestamp


class TestD4TimestampRegression:
    """D4: Timestamp regression fails closed."""

    def test_received_before_exchange_fails(self) -> None:
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        with pytest.raises(DataIntegrityError, match="Causality"):
            validate_temporal_causality(
                exchange_ts=ts,
                received_ts=ts - timedelta(seconds=5),
            )


class TestD5FutureDatedCandle:
    """D5: Future-dated candle causes no look-ahead leakage."""

    def test_future_exchange_timestamp_rejected(self) -> None:
        eval_time = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        future_time = eval_time + timedelta(hours=1)
        with pytest.raises(DataIntegrityError, match="Lookahead"):
            validate_temporal_causality(
                exchange_ts=future_time,
                received_ts=future_time + timedelta(seconds=1),
                evaluation_ts=eval_time,
            )


class TestD6MalformedCandle:
    """D6: Malformed candle data is deterministically rejected."""

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="non-negative"):
            validate_volume(-100)

    def test_invalid_ohlc_high_below_open(self) -> None:
        with pytest.raises(DataIntegrityError, match="High price"):
            validate_ohlc_boundaries(
                open_p=Decimal("100"),
                high_p=Decimal("90"),  # below open
                low_p=Decimal("80"),
                close_p=Decimal("95"),
            )

    def test_invalid_ohlc_low_above_close(self) -> None:
        with pytest.raises(DataIntegrityError, match="Low price"):
            validate_ohlc_boundaries(
                open_p=Decimal("100"),
                high_p=Decimal("110"),
                low_p=Decimal("105"),  # above open and close
                close_p=Decimal("95"),
            )

    def test_zero_price_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="positive"):
            validate_ohlc_boundaries(
                open_p=Decimal("0"),
                high_p=Decimal("100"),
                low_p=Decimal("90"),
                close_p=Decimal("95"),
            )

    def test_malformed_raw_record_rejected(self) -> None:
        with pytest.raises(DataIntegrityError):
            parse_raw_record({"symbol": "", "open": "bad"})

    def test_non_utc_timestamp_rejected(self) -> None:
        naive_ts = datetime(2026, 9, 13, 9, 15)  # No timezone
        with pytest.raises(DataIntegrityError, match="UTC"):
            validate_temporal_causality(
                exchange_ts=naive_ts,
                received_ts=naive_ts,
            )
