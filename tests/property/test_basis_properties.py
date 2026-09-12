"""
Property-based tests for AlphaForge Index-Futures Basis Engine using Hypothesis.
Verifies invariants over randomized input spaces for formulas, skew symmetry, and z-scores.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.basis.calculator import (
    calculate_basis,
    calculate_basis_pct,
    calculate_rolling_stats,
    calculate_timestamp_skew,
)
from alphaforge.basis.enums import BasisZScoreStatus
from alphaforge.core.exceptions import BasisCalculationError


@given(
    index_pts=st.integers(min_value=1000, max_value=100000),
    futures_pts=st.integers(min_value=1000, max_value=100000),
)
@settings(max_examples=100)
def test_basis_calculation_property(index_pts: int, futures_pts: int) -> None:
    """Property: basis = futures_price - index_price and basis_pct = basis / index_price."""
    idx = Decimal(index_pts)
    fut = Decimal(futures_pts)

    basis = calculate_basis(idx, fut)
    assert basis == fut - idx

    basis_pct = calculate_basis_pct(idx, fut)
    assert basis_pct == (fut - idx) / idx


@given(
    neg_or_zero=st.integers(min_value=-10000, max_value=0),
    pos_price=st.integers(min_value=1, max_value=10000),
)
@settings(max_examples=100)
def test_zero_division_safety_property(neg_or_zero: int, pos_price: int) -> None:
    """Property: Index price <= 0 must always raise BasisCalculationError."""
    invalid_price = Decimal(neg_or_zero)
    valid_price = Decimal(pos_price)

    with pytest.raises(BasisCalculationError):
        calculate_basis_pct(invalid_price, valid_price)


@given(
    s1=st.integers(min_value=0, max_value=1000000),
    s2=st.integers(min_value=0, max_value=1000000),
)
@settings(max_examples=100)
def test_timestamp_skew_symmetry_property(s1: int, s2: int) -> None:
    """Property: calculate_timestamp_skew is symmetric and non-negative."""
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    t1 = base + timedelta(seconds=s1)
    t2 = base + timedelta(seconds=s2)

    skew1 = calculate_timestamp_skew(t1, t2)
    skew2 = calculate_timestamp_skew(t2, t1)

    assert skew1 == skew2
    assert skew1 >= Decimal("0")
    assert skew1 == Decimal(abs(s2 - s1))


@given(
    values=st.lists(
        st.integers(min_value=-1000, max_value=1000),
        min_size=20,
        max_size=20,
    )
)
@settings(max_examples=100)
def test_z_score_determinism_property(values: list[int]) -> None:
    """Property: Rolling stats over 20 observations are strictly finite and deterministic."""
    dec_series = [Decimal(v) / Decimal("1000") for v in values]
    stats = calculate_rolling_stats(dec_series, window=20)

    assert stats.count == 20
    assert stats.rolling_mean is not None
    assert stats.rolling_std is not None

    if stats.rolling_std == Decimal("0"):
        assert stats.z_score is None
        assert stats.z_score_status == BasisZScoreStatus.UNDEFINED
    else:
        assert stats.z_score is not None
        assert stats.z_score.is_finite()
        assert stats.z_score_status in (
            BasisZScoreStatus.LOWER,
            BasisZScoreStatus.NORMAL,
            BasisZScoreStatus.HIGHER,
        )
