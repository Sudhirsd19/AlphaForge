"""
AlphaForge Timeframe Definitions and Boundary Validation.
Provides deterministic interval arithmetic and boundary verification for market data candles.
"""

from datetime import datetime, timedelta

from alphaforge.data.enums import Timeframe

TIMEFRAME_DELTAS: dict[str, timedelta] = {
    Timeframe.TF_1M.value: timedelta(minutes=1),
    Timeframe.TF_3M.value: timedelta(minutes=3),
    Timeframe.TF_5M.value: timedelta(minutes=5),
    Timeframe.TF_15M.value: timedelta(minutes=15),
    Timeframe.TF_1H.value: timedelta(hours=1),
    Timeframe.TF_1D.value: timedelta(days=1),
}


def get_timeframe_delta(timeframe: Timeframe | str) -> timedelta:
    """Return the timedelta duration for a given timeframe string or enum."""
    tf_str = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
    if tf_str not in TIMEFRAME_DELTAS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAME_DELTAS[tf_str]


def is_valid_candle_boundary(timestamp: datetime, timeframe: Timeframe | str) -> bool:
    """
    Verify whether a timestamp aligns with canonical timeframe bucket boundaries.
    Candles must align with second=0 and microsecond=0.
    """
    if timestamp.second != 0 or timestamp.microsecond != 0:
        return False

    tf_str = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
    minute = timestamp.minute

    if tf_str == Timeframe.TF_1M.value:
        return True
    elif tf_str == Timeframe.TF_3M.value:
        return (minute % 3) == 0
    elif tf_str == Timeframe.TF_5M.value:
        return (minute % 5) == 0
    elif tf_str == Timeframe.TF_15M.value:
        return (minute % 15) == 0
    elif tf_str == Timeframe.TF_1H.value:
        return minute == 0
    elif tf_str == Timeframe.TF_1D.value:
        return minute == 0 and timestamp.hour == 0

    return False


def expected_next_timestamp(current: datetime, timeframe: Timeframe | str) -> datetime:
    """Compute the deterministic expected next candle timestamp."""
    return current + get_timeframe_delta(timeframe)


def has_candle_gap(
    prev_timestamp: datetime, current_timestamp: datetime, timeframe: Timeframe | str
) -> bool:
    """
    Returns True if current_timestamp is strictly later than the expected next timestamp,
    signaling a missing candle interval.
    """
    delta = get_timeframe_delta(timeframe)
    return current_timestamp > (prev_timestamp + delta)
