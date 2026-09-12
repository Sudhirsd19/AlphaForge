"""
AlphaForge Cost & Slippage Model Enumerations.
Defines decision outcomes and deterministic machine-readable reason codes.
"""

from enum import StrEnum


class CostDecisionState(StrEnum):
    """Core outcome of a cost & slippage evaluation."""

    VALID = "VALID"
    INVALID = "INVALID"


class CostReasonCode(StrEnum):
    """
    Deterministic machine-readable reason codes explaining cost evaluation outcomes.
    All failure cases map to an explicit non-generic code.
    """

    VALID = "VALID"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_MULTIPLIER = "INVALID_MULTIPLIER"
    INVALID_RISK_AMOUNT = "INVALID_RISK_AMOUNT"
    INVALID_FEE_RATE = "INVALID_FEE_RATE"
    INVALID_SLIPPAGE_RATE = "INVALID_SLIPPAGE_RATE"
    INVALID_FIXED_COST = "INVALID_FIXED_COST"
    EFFECTIVE_PRICE_NON_POSITIVE = "EFFECTIVE_PRICE_NON_POSITIVE"
    MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"
    UNKNOWN_COST_STATE = "UNKNOWN_COST_STATE"
