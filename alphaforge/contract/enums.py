"""
AlphaForge Futures Contract Lifecycle Enumerations.
All enumeration members are strings for deterministic serialization and auditability.
"""

from enum import StrEnum


class ContractStatus(StrEnum):
    """
    Deterministic lifecycle states for futures contracts.
    Transitions:
      NOT_YET_LISTED -> ACTIVE
      ACTIVE -> EXPIRING
      EXPIRING -> EXPIRED
      ACTIVE / EXPIRING -> SUSPENDED (if metadata marks suspended)
      Any invalid metadata -> INVALID
    """

    UNKNOWN = "UNKNOWN"
    NOT_YET_LISTED = "NOT_YET_LISTED"
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    INVALID = "INVALID"


class SettlementType(StrEnum):
    """Settlement mechanism for futures contracts."""

    CASH = "CASH"
    PHYSICAL = "PHYSICAL"
