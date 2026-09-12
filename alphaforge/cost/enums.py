from enum import StrEnum


class CostReasonCode(StrEnum):
    """Machine-readable validation/calculation outcome codes for Phase 6."""

    INVALID_INPUT = "INVALID_INPUT"
    INVALID_EFFECTIVE_PRICE = "INVALID_EFFECTIVE_PRICE"
    NON_FINITE_CALCULATION = "NON_FINITE_CALCULATION"
    CALCULATION_COMPLETE = "CALCULATION_COMPLETE"
