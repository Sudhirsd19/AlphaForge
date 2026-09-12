"""
AlphaForge Index-Futures Basis Engine Enumerations.
All enumeration members are strings for deterministic serialization and auditability.
"""

from enum import StrEnum


class BasisStatus(StrEnum):
    """
    Deterministic evaluation status for an index-futures basis observation.
    Captures exact reason when basis calculation is invalid or rejected.
    """

    UNKNOWN = "UNKNOWN"
    VALID = "VALID"
    INVALID = "INVALID"
    STALE = "STALE"
    MISALIGNED_TIMESTAMP = "MISALIGNED_TIMESTAMP"
    FUTURE_DATED_DATA = "FUTURE_DATED_DATA"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    CONTRACT_EXPIRED = "CONTRACT_EXPIRED"
    CONTRACT_SUSPENDED = "CONTRACT_SUSPENDED"
    CONTRACT_NOT_LISTED = "CONTRACT_NOT_LISTED"
    DATA_CONFLICT = "DATA_CONFLICT"
    DATA_GAP = "DATA_GAP"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNDERLYING_MISMATCH = "UNDERLYING_MISMATCH"


class BasisZScoreStatus(StrEnum):
    """
    Deterministic classification of basis z-score against standard baseline thresholds.
    Thresholds:
      LOWER:  z < -2.5
      NORMAL: -2.5 <= z <= 2.5
      HIGHER: z > 2.5
      UNDEFINED: Insufficient history (<20 observations) or zero standard deviation.
    """

    LOWER = "LOWER"
    NORMAL = "NORMAL"
    HIGHER = "HIGHER"
    UNDEFINED = "UNDEFINED"


class BasisConfirmationStatus(StrEnum):
    """
    Deterministic confirmation gate result for strategy/execution input.
    Informational state only; does not directly trigger order execution.
    """

    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    INVALID = "INVALID"
