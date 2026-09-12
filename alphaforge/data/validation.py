"""
AlphaForge Market Data Validation Engine.
Provides pure, deterministic validation functions for market data invariants.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle, RawMarketRecord
from alphaforge.data.timeframe import is_valid_candle_boundary


def validate_decimal_price(name: str, value: Decimal) -> None:
    """Validate that a Decimal price is finite, not NaN, and strictly positive."""
    if value.is_nan() or value.is_infinite() or not value.is_finite():
        raise DataIntegrityError(f"Price '{name}' must be finite and not NaN: {value}")
    if value <= 0:
        raise DataIntegrityError(f"Price '{name}' must be strictly positive: {value}")


def validate_ohlc_boundaries(
    open_p: Decimal, high_p: Decimal, low_p: Decimal, close_p: Decimal
) -> None:
    """Validate mathematical OHLC boundaries."""
    validate_decimal_price("open", open_p)
    validate_decimal_price("high", high_p)
    validate_decimal_price("low", low_p)
    validate_decimal_price("close", close_p)

    max_oc = max(open_p, close_p, low_p)
    if high_p < max_oc:
        raise DataIntegrityError(f"High price {high_p} must be >= max(open, close, low)={max_oc}")

    min_oc = min(open_p, close_p, high_p)
    if low_p > min_oc:
        raise DataIntegrityError(f"Low price {low_p} must be <= min(open, close, high)={min_oc}")


def validate_volume(volume: int) -> None:
    """Validate that volume is non-negative."""
    if volume < 0:
        raise DataIntegrityError(f"Volume must be non-negative: {volume}")


def validate_open_interest(open_interest: int | None) -> None:
    """Validate that open interest is non-negative if specified."""
    if open_interest is not None and open_interest < 0:
        raise DataIntegrityError(f"Open interest must be non-negative: {open_interest}")


def validate_utc_timestamp(name: str, ts: datetime) -> None:
    """Validate that timestamp is explicitly timezone-aware in UTC."""
    if ts.tzinfo is None or ts.utcoffset() != UTC.utcoffset(ts):
        raise DataIntegrityError(f"Timestamp '{name}' must be timezone-aware UTC: {ts}")


def validate_temporal_causality(
    exchange_ts: datetime,
    received_ts: datetime,
    evaluation_ts: datetime | None = None,
) -> None:
    """
    Validate temporal ordering:
    1. received_ts >= exchange_ts (no time travel in reception)
    2. exchange_ts <= evaluation_ts if evaluation_ts is provided (no future lookahead)
    """
    validate_utc_timestamp("exchange_timestamp", exchange_ts)
    validate_utc_timestamp("received_timestamp", received_ts)

    if received_ts < exchange_ts:
        raise DataIntegrityError(
            f"Causality violation: received_timestamp {received_ts} "
            f"precedes exchange_timestamp {exchange_ts}"
        )

    if evaluation_ts is not None:
        validate_utc_timestamp("evaluation_timestamp", evaluation_ts)
        if exchange_ts > evaluation_ts:
            raise DataIntegrityError(
                f"Lookahead violation: exchange_timestamp {exchange_ts} "
                f"is strictly in the future of evaluation_timestamp {evaluation_ts}"
            )


def parse_raw_record(
    record: RawMarketRecord | dict[str, Any],
    evaluation_timestamp: datetime | None = None,
) -> MarketCandle:
    """
    Safely parse and validate an incoming raw market record into a canonical MarketCandle.
    Converts numbers to Decimal and strings to UTC datetimes.
    Raises DataIntegrityError on any invariant violation.
    """
    data = record.model_dump() if isinstance(record, RawMarketRecord) else dict(record)

    # 1. Parse symbol
    raw_sym = str(data.get("symbol", "")).strip().upper()
    if not raw_sym:
        raise DataIntegrityError("Symbol cannot be empty")

    # 2. Parse instrument type
    raw_itype = str(data.get("instrument_type", "")).strip().upper()
    try:
        instrument_type = InstrumentType(raw_itype)
    except ValueError as err:
        raise DataIntegrityError(f"Invalid instrument_type: '{raw_itype}'") from err

    # 3. Parse contract ID
    contract_id = str(data.get("contract_id", "")).strip()
    if not contract_id:
        raise DataIntegrityError("contract_id cannot be empty")

    # 4. Parse timestamps
    def _parse_ts(val: Any, name: str) -> datetime:
        if isinstance(val, datetime):
            validate_utc_timestamp(name, val)
            return val
        if isinstance(val, str):
            try:
                # Handle standard ISO formats, including trailing 'Z'
                clean_val = val.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(clean_val)
                validate_utc_timestamp(name, parsed)
                return parsed
            except Exception as err:
                raise DataIntegrityError(
                    f"Invalid UTC timestamp string for '{name}': '{val}'"
                ) from err
        raise DataIntegrityError(f"Unsupported timestamp format for '{name}': {type(val)}")

    exchange_ts = _parse_ts(data.get("exchange_timestamp"), "exchange_timestamp")
    received_ts = _parse_ts(data.get("received_timestamp"), "received_timestamp")

    # 5. Parse timeframe and boundary
    timeframe = str(data.get("timeframe", "")).strip()
    if not timeframe:
        raise DataIntegrityError("timeframe cannot be empty")
    if not is_valid_candle_boundary(exchange_ts, timeframe):
        raise DataIntegrityError(
            f"Timestamp {exchange_ts} does not align with boundary for timeframe '{timeframe}'"
        )

    # 6. Parse OHLC
    def _parse_dec(val: Any, name: str) -> Decimal:
        try:
            dec = Decimal(str(val))
            validate_decimal_price(name, dec)
            return dec
        except (InvalidOperation, TypeError, ValueError) as err:
            raise DataIntegrityError(f"Invalid decimal value for '{name}': {val}") from err

    open_p = _parse_dec(data.get("open"), "open")
    high_p = _parse_dec(data.get("high"), "high")
    low_p = _parse_dec(data.get("low"), "low")
    close_p = _parse_dec(data.get("close"), "close")

    validate_ohlc_boundaries(open_p, high_p, low_p, close_p)

    # 7. Volume and OI
    try:
        volume = int(data.get("volume", 0))
    except (ValueError, TypeError) as err:
        raise DataIntegrityError(f"Invalid integer volume: {data.get('volume')}") from err
    validate_volume(volume)

    raw_oi = data.get("open_interest")
    oi: int | None = None
    if raw_oi is not None:
        try:
            oi = int(raw_oi)
        except (ValueError, TypeError) as err:
            raise DataIntegrityError(f"Invalid integer open_interest: {raw_oi}") from err
        validate_open_interest(oi)

    # 8. Causality and lookahead validation
    validate_temporal_causality(exchange_ts, received_ts, evaluation_timestamp)

    source = str(data.get("source", "UNKNOWN"))
    is_closed = bool(data.get("is_closed", True))

    default_status = MarketCandle.model_fields["quality_status"].default
    quality_status = data.get("quality_status", default_status)

    return MarketCandle(
        symbol=raw_sym,
        instrument_type=instrument_type,
        contract_id=contract_id,
        exchange_timestamp=exchange_ts,
        received_timestamp=received_ts,
        timeframe=timeframe,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
        open_interest=oi,
        source=source,
        quality_status=quality_status,
        data_version=1,
        is_closed=is_closed,
    )
