"""
Unit tests for contract validation functions.
Verifies lot sizing, sub-lot rejection, tick alignment, and fail-closed price validation.
"""

from decimal import Decimal

import pytest

from alphaforge.contract.validation import (
    is_tick_aligned,
    validate_contract_master,
    validate_lot_quantity,
    validate_price_tick,
)
from alphaforge.core.exceptions import ContractValidationError
from tests.unit.contract.test_contract_models import make_valid_contract


def test_validate_contract_master_valid() -> None:
    """Verify validate_contract_master succeeds on a valid contract."""
    c = make_valid_contract()
    validate_contract_master(c)


def test_validate_contract_master_invalid_type() -> None:
    """Verify validate_contract_master rejects non-ContractMaster inputs."""
    with pytest.raises(ContractValidationError, match="Expected ContractMaster instance"):
        validate_contract_master("invalid_contract")  # type: ignore[arg-type]


def test_validate_lot_quantity_valid() -> None:
    """Verify valid lot quantities (single or integer multiples) pass validation."""
    validate_lot_quantity(quantity=25, lot_size=25)
    validate_lot_quantity(quantity=50, lot_size=25)
    validate_lot_quantity(quantity=250, lot_size=25)
    validate_lot_quantity(quantity=100, lot_size=1)


def test_validate_lot_quantity_sub_lot_rejected() -> None:
    """Test I: Verify quantity smaller than lot_size is strictly rejected."""
    with pytest.raises(ContractValidationError, match="smaller than minimum contract lot size"):
        validate_lot_quantity(quantity=10, lot_size=25)

    with pytest.raises(ContractValidationError, match="smaller than minimum contract lot size"):
        validate_lot_quantity(quantity=24, lot_size=25)


def test_validate_lot_quantity_non_multiple_rejected() -> None:
    """Test J: Verify quantity not an integer multiple of lot_size is strictly rejected."""
    with pytest.raises(ContractValidationError, match="not an integer multiple of lot size"):
        validate_lot_quantity(quantity=26, lot_size=25)

    with pytest.raises(ContractValidationError, match="not an integer multiple of lot size"):
        validate_lot_quantity(quantity=60, lot_size=25)


def test_validate_lot_quantity_non_positive_boundaries() -> None:
    """Test M: Verify zero or negative quantities and lot sizes are rejected."""
    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_lot_quantity(quantity=0, lot_size=25)

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_lot_quantity(quantity=-25, lot_size=25)

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_lot_quantity(quantity=25, lot_size=0)

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_lot_quantity(quantity=25, lot_size=-25)


def test_is_tick_aligned_valid() -> None:
    """Test K: Verify prices that exactly match tick increments return True."""
    assert is_tick_aligned(Decimal("24000.00"), Decimal("0.05")) is True
    assert is_tick_aligned(Decimal("24000.05"), Decimal("0.05")) is True
    assert is_tick_aligned(Decimal("24000.95"), Decimal("0.05")) is True
    assert is_tick_aligned(Decimal("100.25"), Decimal("0.25")) is True
    assert is_tick_aligned(Decimal("100.00"), Decimal("1.00")) is True


def test_is_tick_aligned_invalid() -> None:
    """Verify prices that violate tick increments return False."""
    assert is_tick_aligned(Decimal("24000.02"), Decimal("0.05")) is False
    assert is_tick_aligned(Decimal("24000.03"), Decimal("0.05")) is False
    assert is_tick_aligned(Decimal("100.10"), Decimal("0.25")) is False
    # Non-finite and non-positive prices
    assert is_tick_aligned(Decimal("0"), Decimal("0.05")) is False
    assert is_tick_aligned(Decimal("-10.00"), Decimal("0.05")) is False
    assert is_tick_aligned(Decimal("NaN"), Decimal("0.05")) is False
    assert is_tick_aligned(Decimal("Infinity"), Decimal("0.05")) is False


def test_validate_price_tick_valid() -> None:
    """Verify aligned prices pass validate_price_tick without error."""
    validate_price_tick(Decimal("24500.05"), Decimal("0.05"))
    validate_price_tick(Decimal("100.50"), Decimal("0.25"))


def test_validate_price_tick_misaligned_fails_closed() -> None:
    """Test L: Verify misaligned price raises ContractValidationError without silent rounding."""
    with pytest.raises(ContractValidationError, match="violates tick increment alignment"):
        validate_price_tick(Decimal("24500.03"), Decimal("0.05"))

    with pytest.raises(ContractValidationError, match="violates tick increment alignment"):
        validate_price_tick(Decimal("24500.07"), Decimal("0.05"))


def test_validate_price_tick_non_finite_or_non_positive() -> None:
    """Verify invalid price values raise ContractValidationError."""
    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_price_tick(Decimal("0"), Decimal("0.05"))

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_price_tick(Decimal("-10.00"), Decimal("0.05"))

    with pytest.raises(ContractValidationError, match="finite Decimal"):
        validate_price_tick(Decimal("NaN"), Decimal("0.05"))

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_price_tick(Decimal("100.00"), Decimal("0"))

    with pytest.raises(ContractValidationError, match="strictly positive"):
        validate_price_tick(Decimal("100.00"), Decimal("-0.05"))
