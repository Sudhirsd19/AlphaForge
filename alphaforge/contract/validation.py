"""
AlphaForge Futures Contract Validation Engine.
Provides pure, deterministic validation functions for contract metadata, lot sizing,
and tick alignment.
"""

from decimal import Decimal

from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import ContractValidationError


def validate_contract_master(contract: ContractMaster) -> None:
    """
    Perform exhaustive validation of contract master fields.
    Fails closed on any inconsistency.
    """
    if not isinstance(contract, ContractMaster):
        raise ContractValidationError(f"Expected ContractMaster instance, got: {type(contract)}")

    if not contract.underlying_symbol:
        raise ContractValidationError("Underlying symbol cannot be empty")
    if not contract.contract_id:
        raise ContractValidationError("Contract ID cannot be empty")
    if contract.lot_size <= 0:
        raise ContractValidationError(f"Lot size must be > 0: {contract.lot_size}")
    if contract.tick_size <= Decimal("0"):
        raise ContractValidationError(f"Tick size must be > 0: {contract.tick_size}")
    if contract.contract_multiplier <= Decimal("0"):
        raise ContractValidationError(
            f"Contract multiplier must be > 0: {contract.contract_multiplier}"
        )


def validate_lot_quantity(quantity: int, lot_size: int) -> None:
    """
    Deterministic validation of contract quantity against lot size rules.
    Rules:
      - lot_size must be > 0
      - quantity must be strictly positive
      - quantity smaller than one lot is strictly invalid
      - quantity must be an exact integer multiple of lot_size
    """
    if lot_size <= 0:
        raise ContractValidationError(f"Lot size must be strictly positive: {lot_size}")
    if quantity <= 0:
        raise ContractValidationError(f"Order quantity must be strictly positive: {quantity}")
    if quantity < lot_size:
        raise ContractValidationError(
            f"Order quantity {quantity} is smaller than minimum contract lot size {lot_size}"
        )
    if (quantity % lot_size) != 0:
        raise ContractValidationError(
            f"Order quantity {quantity} is not an integer multiple of lot size {lot_size}"
        )


def is_tick_aligned(price: Decimal, tick_size: Decimal) -> bool:
    """
    Determine if a Decimal price aligns exactly with a Decimal tick size increment.
    Uses exact Decimal arithmetic with zero floating point conversion.
    """
    if not price.is_finite() or price.is_nan() or price <= Decimal("0"):
        return False
    if not tick_size.is_finite() or tick_size.is_nan() or tick_size <= Decimal("0"):
        return False

    remainder = price % tick_size
    return remainder == Decimal("0")


def validate_price_tick(price: Decimal, tick_size: Decimal) -> None:
    """
    Validate that price aligns to tick increments.
    Fails closed with ContractValidationError.
    Strict Quant Invariant: Trading prices must never be silently rounded.
    """
    if not price.is_finite() or price.is_nan():
        raise ContractValidationError(f"Price must be a finite Decimal: {price}")
    if price <= Decimal("0"):
        raise ContractValidationError(f"Price must be strictly positive: {price}")
    if not tick_size.is_finite() or tick_size.is_nan() or tick_size <= Decimal("0"):
        raise ContractValidationError(
            f"Tick size must be a strictly positive finite Decimal: {tick_size}"
        )

    if not is_tick_aligned(price, tick_size):
        raise ContractValidationError(
            f"Price {price} violates tick increment alignment for tick_size {tick_size}"
        )
