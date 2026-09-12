"""
AlphaForge Execution & Order Lifecycle Enumerations.
Defines authoritative states, lifecycle events, reason codes, and watchdog outcomes.
All enum members are strings for deterministic serialization and audit logging.
"""

from enum import StrEnum


class OrderState(StrEnum):
    """
    Authoritative order lifecycle states from the AlphaForge Master Specification.
    Enforces a strict 16-state closed lifecycle vocabulary.
    """

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    EXIT_PENDING = "EXIT_PENDING"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    MANUAL_ESCALATION = "MANUAL_ESCALATION"


class OrderEvent(StrEnum):
    """
    Events triggering deterministic transitions in the Order State Machine.
    """

    VALIDATE_SUCCESS = "VALIDATE_SUCCESS"
    VALIDATE_REJECT = "VALIDATE_REJECT"
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    BROKER_REJECT = "BROKER_REJECT"
    COMMUNICATION_LOST = "COMMUNICATION_LOST"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    REQUEST_PROTECTION = "REQUEST_PROTECTION"
    PROTECTION_CONFIRMED = "PROTECTION_CONFIRMED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    REQUEST_EXIT = "REQUEST_EXIT"
    EXIT_PARTIAL_FILL = "EXIT_PARTIAL_FILL"
    EXIT_FULL_FILL = "EXIT_FULL_FILL"
    START_RECONCILIATION = "START_RECONCILIATION"
    RECONCILE_SUCCESS = "RECONCILE_SUCCESS"
    RECONCILE_FAILED = "RECONCILE_FAILED"
    EMERGENCY_ESCALATE = "EMERGENCY_ESCALATE"


class OrderSide(StrEnum):
    """Order execution direction."""

    BUY = "BUY"
    SELL = "SELL"


class ExecutionReasonCode(StrEnum):
    """
    Deterministic machine-readable reason codes explaining lifecycle outcomes.
    """

    OK = "OK"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    ALREADY_IN_TARGET_STATE = "ALREADY_IN_TARGET_STATE"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    TERMINAL_STATE = "TERMINAL_STATE"
    INVALID_STATE = "INVALID_STATE"
    INVALID_EVENT = "INVALID_EVENT"
    MISSING_REQUIRED_CONFIRMATION = "MISSING_REQUIRED_CONFIRMATION"
    PROTECTION_REQUIRED = "PROTECTION_REQUIRED"
    UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_DIRECTION = "INVALID_DIRECTION"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    TIMEOUT_EXCEEDED = "TIMEOUT_EXCEEDED"
    MANUAL_ESCALATION_REQUIRED = "MANUAL_ESCALATION_REQUIRED"


class WatchdogStatus(StrEnum):
    """Health status reported by the Emergency Protection Watchdog."""

    SAFE = "SAFE"
    UNPROTECTED_HAZARD = "UNPROTECTED_HAZARD"
    TIMEOUT_BREACH = "TIMEOUT_BREACH"
    ESCALATED = "ESCALATED"
