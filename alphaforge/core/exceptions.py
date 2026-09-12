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


class ContractValidationError(DataIntegrityError):
    """Raised when contract master metadata or lifecycle invariants are breached."""

    pass


class BasisCalculationError(AlphaForgeError):
    """Raised when index-futures basis calculation or validation invariants fail."""

    pass


class RiskError(AlphaForgeError):
    """Base exception for all risk engine and validation errors."""

    pass


class RiskValidationError(RiskError):
    """Raised when risk inputs, configurations, or state invariants are breached."""

    pass


class CostError(AlphaForgeError):
    """Base exception for all cost and slippage model errors."""

    pass


class CostValidationError(CostError):
    """Raised when cost inputs, configurations, or calculation invariants are breached."""

    pass


class ExecutionError(AlphaForgeError):
    """Base exception for all execution and order lifecycle errors."""

    pass


class IllegalStateTransitionError(ExecutionError):
    """Raised when an illegal order state transition is attempted."""

    pass


class UnprotectedPositionError(ExecutionError):
    """Raised when an emergency unprotected position hazard is detected."""

    pass


class OrderValidationError(ExecutionError):
    """Raised when order parameters, quantities, or metadata violate invariants."""

    pass
