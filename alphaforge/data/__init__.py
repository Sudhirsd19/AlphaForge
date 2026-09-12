"""
AlphaForge Market Data Layer.
"""

from alphaforge.data.enums import (
    EXECUTION_BLOCKING_STATUSES,
    QUALITY_PRECEDENCE_RANK,
    RECOVERABLE_STATUSES,
    DataQualityStatus,
    InstrumentType,
    Timeframe,
    is_execution_eligible,
    most_severe_status,
)
from alphaforge.data.models import MarketCandle, RawMarketRecord
from alphaforge.data.normalization import (
    MarketDataNormalizer,
    NormalizationResult,
    QuarantinedRecord,
)
from alphaforge.data.store import CandleStore
from alphaforge.data.timeframe import (
    TIMEFRAME_DELTAS,
    expected_next_timestamp,
    get_timeframe_delta,
    has_candle_gap,
    is_valid_candle_boundary,
)
from alphaforge.data.validation import (
    parse_raw_record,
    validate_decimal_price,
    validate_ohlc_boundaries,
    validate_open_interest,
    validate_temporal_causality,
    validate_utc_timestamp,
    validate_volume,
)

__all__ = [
    "EXECUTION_BLOCKING_STATUSES",
    "QUALITY_PRECEDENCE_RANK",
    "RECOVERABLE_STATUSES",
    "CandleStore",
    "DataQualityStatus",
    "InstrumentType",
    "MarketCandle",
    "MarketDataNormalizer",
    "NormalizationResult",
    "QuarantinedRecord",
    "RawMarketRecord",
    "TIMEFRAME_DELTAS",
    "Timeframe",
    "expected_next_timestamp",
    "get_timeframe_delta",
    "has_candle_gap",
    "is_execution_eligible",
    "is_valid_candle_boundary",
    "most_severe_status",
    "parse_raw_record",
    "validate_decimal_price",
    "validate_ohlc_boundaries",
    "validate_open_interest",
    "validate_temporal_causality",
    "validate_utc_timestamp",
    "validate_volume",
]
