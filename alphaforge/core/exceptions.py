"""
AlphaForge Core Domain Exceptions.
"""


class AlphaForgeError(Exception):
    """Base exception for all AlphaForge domain errors."""

    pass


class ClosedCandleViolationError(AlphaForgeError):
    """Raised when an attempt is made to evaluate forming candle [0] for signals."""

    pass


class InsufficientDataError(AlphaForgeError):
    """Raised when history is insufficient to compute required indicators."""

    pass


class DataIntegrityError(AlphaForgeError):
    """Raised when candle OHLC or timestamp invariants are violated."""

    pass


class StaleDataError(AlphaForgeError):
    """Raised when market data exceeds maximum permitted age."""

    pass


class InvalidBracketError(AlphaForgeError):
    """Raised when stop-loss or profit-target prices fail validation."""

    pass


class NonDeterministicError(AlphaForgeError):
    """Raised when a non-deterministic execution anomaly is detected."""

    pass
