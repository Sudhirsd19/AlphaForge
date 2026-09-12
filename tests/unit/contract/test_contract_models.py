"""
Unit tests for ContractMaster model in AlphaForge.
Verifies immutability, 19 fields, UTC enforcement, Decimal precision, and temporal invariants.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import ContractValidationError
from alphaforge.data.enums import InstrumentType


def make_valid_contract(**overrides: object) -> ContractMaster:
    """Helper to generate a valid canonical ContractMaster instance."""
    now = datetime(2026, 6, 1, 9, 15, 0, tzinfo=UTC)
    params: dict[str, object] = {
        "exchange": "NSE",
        "segment": "NFO",
        "underlying_symbol": "NIFTY",
        "contract_id": "NIFTY26JUNFUT",
        "instrument_type": InstrumentType.FUTURES,
        "listing_datetime": now - timedelta(days=90),
        "trading_start_datetime": now - timedelta(days=90),
        "trading_end_datetime": now + timedelta(days=25, hours=6),
        "expiry_datetime": now + timedelta(days=25, hours=6),
        "lot_size": 25,
        "tick_size": Decimal("0.05"),
        "contract_multiplier": Decimal("1"),
        "price_decimal_places": 2,
        "quantity_decimal_places": 0,
        "currency": "INR",
        "settlement_type": SettlementType.CASH,
        "status": ContractStatus.ACTIVE,
        "data_source": "NSE_CONTRACT_REF",
        "contract_version": 1,
        "is_suspended": False,
    }
    params.update(overrides)
    return ContractMaster(**params)  # type: ignore[arg-type]


def test_contract_master_valid_creation() -> None:
    """Verify standard contract master can be constructed and all 19 fields are accessible."""
    c = make_valid_contract()
    assert c.exchange == "NSE"
    assert c.segment == "NFO"
    assert c.underlying_symbol == "NIFTY"
    assert c.contract_id == "NIFTY26JUNFUT"
    assert c.instrument_type == InstrumentType.FUTURES
    assert c.lot_size == 25
    assert c.tick_size == Decimal("0.05")
    assert c.contract_multiplier == Decimal("1")
    assert c.price_decimal_places == 2
    assert c.quantity_decimal_places == 0
    assert c.currency == "INR"
    assert c.settlement_type == SettlementType.CASH
    assert c.status == ContractStatus.ACTIVE
    assert c.data_source == "NSE_CONTRACT_REF"
    assert c.contract_version == 1
    assert c.is_suspended is False


def test_contract_master_immutability() -> None:
    """Verify ContractMaster is frozen and prevents field reassignment."""
    c = make_valid_contract()
    with pytest.raises(ValidationError):
        c.lot_size = 50


def test_contract_master_extra_fields_forbidden() -> None:
    """Verify extra fields are strictly forbidden."""
    with pytest.raises(ValidationError):
        make_valid_contract(extra_field="unauthorized")


def test_contract_master_uppercase_enforcement() -> None:
    """Verify string identifiers must be uppercase non-empty strings."""
    with pytest.raises(ContractValidationError):
        make_valid_contract(exchange="nse")

    with pytest.raises(ContractValidationError):
        make_valid_contract(underlying_symbol="nifty")

    with pytest.raises(ContractValidationError):
        make_valid_contract(contract_id="nifty26junfut")

    with pytest.raises(ContractValidationError):
        make_valid_contract(currency="inr")


def test_contract_master_timezone_validation() -> None:
    """Verify timestamps must be explicitly timezone-aware UTC."""
    naive_dt = datetime(2026, 6, 1, 9, 15, 0)
    with pytest.raises(ContractValidationError):
        make_valid_contract(listing_datetime=naive_dt)

    with pytest.raises(ContractValidationError):
        make_valid_contract(trading_start_datetime=naive_dt)

    with pytest.raises(ContractValidationError):
        make_valid_contract(trading_end_datetime=naive_dt)

    with pytest.raises(ContractValidationError):
        make_valid_contract(expiry_datetime=naive_dt)


def test_contract_master_decimal_precision() -> None:
    """Verify tick_size and contract_multiplier must be finite positive Decimals."""
    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(tick_size=Decimal("NaN"))

    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(tick_size=Decimal("Infinity"))

    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(tick_size=Decimal("0"))

    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(tick_size=Decimal("-0.05"))

    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(contract_multiplier=Decimal("0"))


def test_contract_master_lot_size_boundary() -> None:
    """Verify lot size must be strictly positive integer."""
    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(lot_size=0)

    with pytest.raises((ContractValidationError, ValidationError)):
        make_valid_contract(lot_size=-25)


def test_contract_master_temporal_invariants() -> None:
    """
    Verify strict temporal order:
    listing_datetime <= trading_start_datetime < trading_end_datetime <= expiry_datetime
    """
    t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC)
    t3 = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    t4 = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)

    # Valid exact equality on boundaries: listing == trading_start and trading_end == expiry
    c = make_valid_contract(
        listing_datetime=t1,
        trading_start_datetime=t1,
        trading_end_datetime=t4,
        expiry_datetime=t4,
    )
    assert c is not None

    # Violation 1: listing_datetime > trading_start_datetime
    with pytest.raises(ContractValidationError):
        make_valid_contract(
            listing_datetime=t2,
            trading_start_datetime=t1,
            trading_end_datetime=t3,
            expiry_datetime=t4,
        )

    # Violation 2: trading_start_datetime >= trading_end_datetime
    with pytest.raises(ContractValidationError):
        make_valid_contract(
            listing_datetime=t1,
            trading_start_datetime=t3,
            trading_end_datetime=t2,
            expiry_datetime=t4,
        )

    with pytest.raises(ContractValidationError):
        make_valid_contract(
            listing_datetime=t1,
            trading_start_datetime=t3,
            trading_end_datetime=t3,
            expiry_datetime=t4,
        )

    # Violation 3: trading_end_datetime > expiry_datetime
    with pytest.raises(ContractValidationError):
        make_valid_contract(
            listing_datetime=t1,
            trading_start_datetime=t2,
            trading_end_datetime=t4 + timedelta(hours=1),
            expiry_datetime=t4,
        )
