"""
Property-based tests for AlphaForge Futures Contract Engine using Hypothesis.
Verifies invariants over randomized inputs for lots, ticks, and lifecycle state.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.lifecycle import evaluate_contract_lifecycle
from alphaforge.contract.validation import (
    is_tick_aligned,
    validate_lot_quantity,
    validate_price_tick,
)
from alphaforge.core.exceptions import ContractValidationError
from tests.unit.contract.test_contract_models import make_valid_contract


@given(
    lot_size=st.integers(min_value=1, max_value=500),
    multiplier=st.integers(min_value=1, max_value=200),
)
@settings(max_examples=100)
def test_lot_quantity_valid_multiples_property(lot_size: int, multiplier: int) -> None:
    """Property: Any exact positive integer multiple of lot_size must pass validation."""
    valid_qty = lot_size * multiplier
    validate_lot_quantity(valid_qty, lot_size)


@given(
    lot_size=st.integers(min_value=2, max_value=500),
    multiplier=st.integers(min_value=0, max_value=200),
    remainder=st.integers(min_value=1, max_value=499),
)
@settings(max_examples=100)
def test_lot_quantity_invalid_remainders_property(
    lot_size: int, multiplier: int, remainder: int
) -> None:
    """Property: Quantity with remainder or sub-lot must raise ContractValidationError."""
    rem = remainder % lot_size
    if rem == 0:
        rem = 1  # ensure non-zero remainder
    invalid_qty = (lot_size * multiplier) + rem
    with pytest.raises(ContractValidationError):
        validate_lot_quantity(invalid_qty, lot_size)


@given(
    k=st.integers(min_value=1, max_value=100000),
    tick_cents=st.sampled_from([5, 10, 25, 50, 100]),
)
@settings(max_examples=100)
def test_tick_alignment_multiples_property(k: int, tick_cents: int) -> None:
    """Property: Any exact multiple of tick_size is aligned and passes validation."""
    tick_size = Decimal(tick_cents) / Decimal("100")
    price = Decimal(k) * tick_size
    assert is_tick_aligned(price, tick_size) is True
    validate_price_tick(price, tick_size)


@given(
    k=st.integers(min_value=1, max_value=100000),
    offset_cents=st.sampled_from([1, 2, 3, 4]),
)
@settings(max_examples=100)
def test_tick_alignment_misaligned_property(k: int, offset_cents: int) -> None:
    """Property: Any price misaligned against 0.05 tick size raises ContractValidationError."""
    tick_size = Decimal("0.05")
    # Base price on 0.05 tick increment + offset cents that are not multiples of 5
    price = (Decimal(k) * tick_size) + (Decimal(offset_cents) / Decimal("100"))
    assert is_tick_aligned(price, tick_size) is False
    with pytest.raises(ContractValidationError):
        validate_price_tick(price, tick_size)


def test_lifecycle_monotonic_progression_property() -> None:
    """
    Property: For a chronological sequence of evaluation timestamps from before listing
    to after expiry, the contract state monotonically advances:
    NOT_YET_LISTED -> ACTIVE -> EXPIRING -> EXPIRED.
    """
    t_list = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    t_start = datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC)
    t_exp = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=t_list,
        trading_start_datetime=t_start,
        trading_end_datetime=t_exp,
        expiry_datetime=t_exp,
    )

    timestamps = [
        t_list - timedelta(days=5),
        t_list,
        t_start - timedelta(minutes=1),
        t_start,
        t_start + timedelta(days=30),
        t_exp - timedelta(hours=3),
        t_exp - timedelta(hours=1),
        t_exp,
        t_exp + timedelta(days=1),
    ]

    expected = [
        ContractStatus.NOT_YET_LISTED,
        ContractStatus.NOT_YET_LISTED,
        ContractStatus.NOT_YET_LISTED,
        ContractStatus.ACTIVE,
        ContractStatus.ACTIVE,
        ContractStatus.ACTIVE,
        ContractStatus.EXPIRING,
        ContractStatus.EXPIRED,
        ContractStatus.EXPIRED,
    ]

    actual = [evaluate_contract_lifecycle(c, ts) for ts in timestamps]
    assert actual == expected
