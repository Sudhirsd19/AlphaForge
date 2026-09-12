"""
Unit tests for Index-Futures Basis Calculator in AlphaForge.
Tests absolute basis, normalized basis, timestamp skew, and rolling z-score statistics.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.basis.calculator import (
    calculate_basis,
    calculate_basis_pct,
    calculate_rolling_stats,
    calculate_timestamp_skew,
)
from alphaforge.basis.enums import BasisZScoreStatus
from alphaforge.core.exceptions import BasisCalculationError


def test_test_a_valid_absolute_basis() -> None:
    """Test A: Valid absolute basis calculation in points."""
    idx = Decimal("24000.00")
    fut = Decimal("24050.00")
    basis = calculate_basis(idx, fut)
    assert basis == Decimal("50.00")

    # Negative basis (backwardation / discount)
    fut_disc = Decimal("23950.00")
    assert calculate_basis(idx, fut_disc) == Decimal("-50.00")


def test_test_b_valid_basis_percentage() -> None:
    """Test B: Valid normalized basis percentage calculation."""
    idx = Decimal("20000.00")
    fut = Decimal("20100.00")
    # basis = 100, basis_pct = 100 / 20000 = 0.005 (0.5%)
    basis_pct = calculate_basis_pct(idx, fut)
    assert basis_pct == Decimal("0.005")

    # Negative basis_pct
    fut_disc = Decimal("19900.00")
    assert calculate_basis_pct(idx, fut_disc) == Decimal("-0.005")


def test_test_c_zero_index_price_rejected() -> None:
    """Test C: Zero index price must fail closed with BasisCalculationError."""
    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis(Decimal("0"), Decimal("24000.00"))

    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis_pct(Decimal("0"), Decimal("24000.00"))


def test_test_d_negative_price_rejected() -> None:
    """Test D: Negative index or futures prices must fail closed."""
    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis(Decimal("-24000.00"), Decimal("24050.00"))

    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis(Decimal("24000.00"), Decimal("-24050.00"))

    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis_pct(Decimal("-24000.00"), Decimal("24050.00"))

    with pytest.raises(BasisCalculationError, match="strictly positive"):
        calculate_basis_pct(Decimal("24000.00"), Decimal("-24050.00"))


def test_calculator_non_finite_price_rejected() -> None:
    """Verify non-finite Decimals (NaN, Infinity) are rejected."""
    with pytest.raises(BasisCalculationError, match="finite Decimal"):
        calculate_basis(Decimal("NaN"), Decimal("24050.00"))

    with pytest.raises(BasisCalculationError, match="finite Decimal"):
        calculate_basis(Decimal("24000.00"), Decimal("Infinity"))


def test_test_j_k_l_timestamp_skew() -> None:
    """Tests J, K, L: Timestamp skew calculation and threshold comparisons."""
    t1 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    t2 = t1 + timedelta(seconds=30)
    t3 = t1 + timedelta(seconds=60)
    t4 = t1 + timedelta(seconds=90)

    # Test J: within threshold (30s)
    skew_within = calculate_timestamp_skew(t1, t2)
    assert skew_within == Decimal("30.0")
    assert skew_within < Decimal("60")

    # Test K: exactly at threshold (60s)
    skew_at = calculate_timestamp_skew(t1, t3)
    assert skew_at == Decimal("60.0")
    assert skew_at <= Decimal("60")

    # Test L: beyond threshold (90s)
    skew_beyond = calculate_timestamp_skew(t1, t4)
    assert skew_beyond == Decimal("90.0")
    assert skew_beyond > Decimal("60")

    # Symmetry
    assert calculate_timestamp_skew(t4, t1) == Decimal("90.0")


def test_timestamp_skew_naive_rejected() -> None:
    """Verify timezone-naive timestamps are rejected."""
    naive_t = datetime(2026, 6, 1, 10, 0, 0)
    utc_t = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    with pytest.raises(BasisCalculationError, match="explicitly timezone-aware UTC"):
        calculate_timestamp_skew(naive_t, utc_t)


def test_test_x_insufficient_history() -> None:
    """Test X: Insufficient history (< 20 observations) returns UNDEFINED status."""
    history = [Decimal("0.002") for _ in range(19)]
    stats = calculate_rolling_stats(history, window=20)
    assert stats.count == 19
    assert stats.z_score_status == BasisZScoreStatus.UNDEFINED
    assert stats.rolling_mean is None
    assert stats.rolling_std is None
    assert stats.z_score is None


def test_test_w_zero_standard_deviation_handled_safely() -> None:
    """Test W: Zero standard deviation returns UNDEFINED and never divides by zero."""
    history = [Decimal("0.005") for _ in range(20)]
    stats = calculate_rolling_stats(history, window=20)
    assert stats.count == 20
    assert stats.rolling_mean == Decimal("0.005")
    assert stats.rolling_std == Decimal("0")
    assert stats.z_score is None
    assert stats.z_score_status == BasisZScoreStatus.UNDEFINED


def test_test_t_u_v_y_rolling_stats_and_zscore() -> None:
    """Tests T, U, V, Y: Rolling mean, standard deviation, and z-score for 20 observations."""
    # 19 values of 0.01 and 1 value of 0.03
    # mean = (19 * 0.01 + 0.03) / 20 = 0.22 / 20 = 0.011
    history = [Decimal("0.01")] * 19 + [Decimal("0.03")]
    stats = calculate_rolling_stats(history, window=20)
    assert stats.count == 20
    assert stats.rolling_mean == Decimal("0.011")
    assert stats.rolling_std is not None
    assert stats.rolling_std > Decimal("0")
    assert stats.z_score is not None
    # Latest value (0.03) is significantly higher than mean (0.011)
    assert stats.z_score > Decimal("2.5")
    assert stats.z_score_status == BasisZScoreStatus.HIGHER


def test_z_score_threshold_classification() -> None:
    """Verify LOWER, NORMAL, and HIGHER threshold classifications."""
    # Create history with known mean 0.010 and positive variance
    # Values: 0.000 to 0.019 (20 values)
    history = [Decimal(i) / Decimal("1000") for i in range(20)]
    stats = calculate_rolling_stats(history, window=20)
    assert stats.count == 20
    assert stats.rolling_mean is not None
    assert stats.rolling_std is not None
    assert stats.z_score is not None
    # With evenly spaced values, the last value is within ~1.65 std -> NORMAL
    assert stats.z_score_status == BasisZScoreStatus.NORMAL

    # Large negative outlier -> LOWER
    history_lower = [Decimal("0.01")] * 19 + [Decimal("-0.03")]
    stats_lower = calculate_rolling_stats(history_lower, window=20)
    assert stats_lower.z_score is not None
    assert stats_lower.z_score < Decimal("-2.5")
    assert stats_lower.z_score_status == BasisZScoreStatus.LOWER


def test_trailing_window_over_21_observations() -> None:
    """Verify when history has 21 observations, only trailing 20 are used."""
    # First observation is an extreme outlier (0.999), followed by 20 values (0.001..0.020)
    history = [Decimal("0.999")] + [Decimal(i) / Decimal("1000") for i in range(1, 21)]
    assert len(history) == 21
    stats = calculate_rolling_stats(history, window=20)
    assert stats.count == 20
    # Mean of 0.001 to 0.020 is (0.001 + 0.020)/2 = 0.0105
    assert stats.rolling_mean == Decimal("0.0105")
