"""
AlphaForge Core Enumerations.
All enumeration members are strings for deterministic serialization and audit logging.
"""

from enum import StrEnum


class SignalDirection(StrEnum):
    """Direction of trade setup."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class StrategyDecision(StrEnum):
    """Deterministic outcome of strategy evaluation."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    INVALID_DATA = "INVALID_DATA"
    EXPIRED = "EXPIRED"
    DUPLICATE = "DUPLICATE"


class RejectionCode(StrEnum):
    """Formal deterministic rejection reason codes."""

    REJECT_NONE = "REJECT_NONE"
    REJECT_CLOSED_CANDLE_VIOLATION = "REJECT_CLOSED_CANDLE_VIOLATION"
    REJECT_DATA_STALE = "REJECT_DATA_STALE"
    REJECT_DATA_INVALID = "REJECT_DATA_INVALID"
    REJECT_MISSING_TIMEFRAME = "REJECT_MISSING_TIMEFRAME"
    REJECT_INSUFFICIENT_HISTORY = "REJECT_INSUFFICIENT_HISTORY"
    REJECT_TREND = "REJECT_TREND"
    REJECT_BREAKOUT = "REJECT_BREAKOUT"
    REJECT_CANDLE_GEOMETRY = "REJECT_CANDLE_GEOMETRY"
    REJECT_VOLUME = "REJECT_VOLUME"
    REJECT_MOMENTUM = "REJECT_MOMENTUM"
    REJECT_VOLATILITY = "REJECT_VOLATILITY"
    REJECT_FUTURES_CONFIRMATION = "REJECT_FUTURES_CONFIRMATION"
    REJECT_INVALID_STOP = "REJECT_INVALID_STOP"
    REJECT_INVALID_TARGET = "REJECT_INVALID_TARGET"
    REJECT_EXPIRED = "REJECT_EXPIRED"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    REJECT_ENTRY_CUTOFF = "REJECT_ENTRY_CUTOFF"


class FuturesConfirmationStatus(StrEnum):
    """Status contract of index-futures confirmation."""

    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    INVALID = "INVALID"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class TrendState(StrEnum):
    """Higher-timeframe trend regime."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
