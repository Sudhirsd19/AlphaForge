"""
Unit tests for Market Data Validation Engine.
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.models import RawMarketRecord
from alphaforge.data.validation import (
    parse_raw_record,
    validate_decimal_price,
    validate_ohlc_boundaries,
    validate_open_interest,
    validate_temporal_causality,
    validate_utc_timestamp,
    validate_volume,
)


def test_validate_decimal_price_positive() -> None:
    """Strictly positive finite decimals must pass."""
    validate_decimal_price("test_price", Decimal("100.50"))
    validate_decimal_price("test_price", Decimal("0.01"))


def test_validate_decimal_price_invalid() -> None:
    """Zero, negative, NaN, and infinity values must raise DataIntegrityError."""
    with pytest.raises(DataIntegrityError, match="strictly positive"):
        validate_decimal_price("zero_price", Decimal("0.00"))

    with pytest.raises(DataIntegrityError, match="strictly positive"):
        validate_decimal_price("neg_price", Decimal("-10.50"))

    with pytest.raises(DataIntegrityError, match="not NaN"):
        validate_decimal_price("nan_price", Decimal("NaN"))

    with pytest.raises(DataIntegrityError, match="finite"):
        validate_decimal_price("inf_price", Decimal("Infinity"))


def test_validate_ohlc_boundaries_valid() -> None:
    """Valid OHLC boundaries must pass."""
    validate_ohlc_boundaries(
        Decimal("25000.00"), Decimal("25100.00"), Decimal("24950.00"), Decimal("25050.00")
    )


def test_validate_ohlc_boundaries_breaches() -> None:
    """Violations of high >= max(open, close, low) and low <= min(...) must fail."""
    # High lower than open
    with pytest.raises(DataIntegrityError, match="High price"):
        validate_ohlc_boundaries(
            Decimal("25100.00"), Decimal("25050.00"), Decimal("24950.00"), Decimal("25000.00")
        )

    # High lower than close
    with pytest.raises(DataIntegrityError, match="High price"):
        validate_ohlc_boundaries(
            Decimal("25000.00"), Decimal("25050.00"), Decimal("24950.00"), Decimal("25100.00")
        )

    # Low higher than open
    with pytest.raises(DataIntegrityError, match="Low price"):
        validate_ohlc_boundaries(
            Decimal("24900.00"), Decimal("25100.00"), Decimal("24950.00"), Decimal("25000.00")
        )

    # Low higher than close
    with pytest.raises(DataIntegrityError, match="Low price"):
        validate_ohlc_boundaries(
            Decimal("25000.00"), Decimal("25100.00"), Decimal("24950.00"), Decimal("24900.00")
        )


def test_validate_volume() -> None:
    """Volume must be non-negative. Zero volume is explicitly valid."""
    validate_volume(0)
    validate_volume(1000)

    with pytest.raises(DataIntegrityError, match="Volume must be non-negative"):
        validate_volume(-1)


def test_validate_open_interest() -> None:
    """Open interest can be None or >= 0."""
    validate_open_interest(None)
    validate_open_interest(0)
    validate_open_interest(50000)

    with pytest.raises(DataIntegrityError, match="Open interest must be non-negative"):
        validate_open_interest(-100)


def test_validate_utc_timestamp() -> None:
    """Timestamps must have tzinfo and UTC offset 0."""
    valid_utc = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    validate_utc_timestamp("valid_ts", valid_utc)

    # Naive timestamp
    naive_ts = datetime(2026, 9, 11, 9, 15)
    with pytest.raises(DataIntegrityError, match="timezone-aware UTC"):
        validate_utc_timestamp("naive_ts", naive_ts)

    # Non-UTC timezone (e.g. IST +05:30)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_ts = datetime(2026, 9, 11, 14, 45, tzinfo=ist_tz)
    with pytest.raises(DataIntegrityError, match="timezone-aware UTC"):
        validate_utc_timestamp("ist_ts", ist_ts)


def test_validate_temporal_causality() -> None:
    """
    received_timestamp must be >= exchange_timestamp,
    and exchange_timestamp <= evaluation_timestamp.
    """
    t_ex = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t_rx = datetime(2026, 9, 11, 9, 15, 1, tzinfo=UTC)
    t_eval = datetime(2026, 9, 11, 9, 16, tzinfo=UTC)

    # Valid causality
    validate_temporal_causality(t_ex, t_rx, t_eval)

    # Causality violation: received before exchange
    with pytest.raises(DataIntegrityError, match="Causality violation"):
        validate_temporal_causality(t_ex, datetime(2026, 9, 11, 9, 14, 59, tzinfo=UTC), t_eval)

    # Lookahead violation: exchange candle timestamp in the future of evaluation
    with pytest.raises(DataIntegrityError, match="Lookahead violation"):
        validate_temporal_causality(
            datetime(2026, 9, 11, 9, 20, tzinfo=UTC),
            datetime(2026, 9, 11, 9, 20, 1, tzinfo=UTC),
            evaluation_ts=datetime(2026, 9, 11, 9, 18, tzinfo=UTC),
        )


def test_parse_raw_record_valid() -> None:
    """Raw record with string prices and ISO timestamps parses into canonical MarketCandle."""
    raw = RawMarketRecord(
        symbol="NIFTY",
        instrument_type="FUTURES",
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp="2026-09-11T09:15:00Z",
        received_timestamp="2026-09-11T09:15:01Z",
        timeframe="3m",
        open="25000.00",
        high="25050.00",
        low="24980.00",
        close="25020.00",
        volume=10000,
        open_interest=400000,
        source="TEST_FEED",
    )

    candle = parse_raw_record(raw)
    assert candle.symbol == "NIFTY"
    assert candle.open == Decimal("25000.00")
    assert candle.high == Decimal("25050.00")
    assert candle.low == Decimal("24980.00")
    assert candle.close == Decimal("25020.00")
    assert candle.volume == 10000
    assert candle.timeframe == "3m"


def test_parse_raw_record_boundary_misalignment() -> None:
    """Candle timestamp misaligned with timeframe boundary must fail."""
    # 3m candle with timestamp at 09:16 (not % 3 == 0)
    raw = {
        "symbol": "NIFTY",
        "instrument_type": "FUTURES",
        "contract_id": "NIFTY26SEPFUT",
        "exchange_timestamp": "2026-09-11T09:16:00Z",
        "received_timestamp": "2026-09-11T09:16:01Z",
        "timeframe": "3m",
        "open": "25000.00",
        "high": "25050.00",
        "low": "24980.00",
        "close": "25020.00",
        "volume": 1000,
    }
    with pytest.raises(DataIntegrityError, match="does not align with boundary"):
        parse_raw_record(raw)
