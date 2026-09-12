"""
AlphaForge Index-Futures Basis Pure Calculation Engine.
Provides pure, deterministic functions for basis, normalized basis,
timestamp skew, and rolling statistics.
Zero floating-point arithmetic.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.basis.enums import BasisZScoreStatus
from alphaforge.basis.models import RollingBasisStats
from alphaforge.core.exceptions import BasisCalculationError


def calculate_basis(index_price: Decimal, futures_price: Decimal) -> Decimal:
    """
    Calculate absolute basis in points: futures_price - index_price.
    Fails closed on non-finite or non-positive price inputs.
    Units: points (Decimal).
    """
    if not index_price.is_finite() or index_price.is_nan():
        raise BasisCalculationError(f"index_price must be finite Decimal: {index_price}")
    if not futures_price.is_finite() or futures_price.is_nan():
        raise BasisCalculationError(f"futures_price must be finite Decimal: {futures_price}")
    if index_price <= Decimal("0"):
        raise BasisCalculationError(f"index_price must be strictly positive: {index_price}")
    if futures_price <= Decimal("0"):
        raise BasisCalculationError(f"futures_price must be strictly positive: {futures_price}")

    return futures_price - index_price


def calculate_basis_pct(index_price: Decimal, futures_price: Decimal) -> Decimal:
    """
    Calculate normalized basis percentage ratio: (futures_price - index_price) / index_price.
    Fails closed on non-finite inputs or invalid denominator (index_price <= 0).
    Units: decimal ratio / percentage (Decimal).
    """
    basis = calculate_basis(index_price, futures_price)
    return basis / index_price


def calculate_timestamp_skew(index_timestamp: datetime, futures_timestamp: datetime) -> Decimal:
    """
    Calculate absolute temporal skew between index and futures timestamps in seconds.
    Both timestamps must be explicitly UTC timezone-aware.
    Units: seconds (Decimal).
    """
    if index_timestamp.tzinfo is None or index_timestamp.utcoffset() != UTC.utcoffset(
        index_timestamp
    ):
        raise BasisCalculationError(
            f"index_timestamp must be explicitly timezone-aware UTC: {index_timestamp}"
        )
    if futures_timestamp.tzinfo is None or futures_timestamp.utcoffset() != UTC.utcoffset(
        futures_timestamp
    ):
        raise BasisCalculationError(
            f"futures_timestamp must be explicitly timezone-aware UTC: {futures_timestamp}"
        )

    skew = abs((futures_timestamp - index_timestamp).total_seconds())
    return Decimal(str(skew))


def calculate_rolling_stats(
    basis_pct_history: Sequence[Decimal],
    window: int = 20,
    lower_threshold: Decimal = Decimal("-2.5"),
    upper_threshold: Decimal = Decimal("2.5"),
) -> RollingBasisStats:
    """
    Calculate deterministic rolling mean, sample standard deviation, and z-score
    over basis_pct observations.
    Baseline window: 20 observations.
    Formula:
      - mean = sum(sample) / window
      - sample_variance = sum((x - mean)^2) / (window - 1)
      - sample_std = sqrt(sample_variance)
      - z = (current_basis_pct - mean) / sample_std
    Fail-closed protections:
      - If len(basis_pct_history) < window: returns UNDEFINED (insufficient history).
      - If sample_std == 0: returns UNDEFINED (never divide by zero).
    """
    if window < 2:
        raise BasisCalculationError(f"Rolling window must be >= 2: {window}")

    if len(basis_pct_history) < window:
        return RollingBasisStats(
            count=len(basis_pct_history),
            window_size=window,
            z_score_status=BasisZScoreStatus.UNDEFINED,
        )

    # Take the most recent `window` observations
    sample = list(basis_pct_history[-window:])
    for val in sample:
        if not val.is_finite() or val.is_nan():
            raise BasisCalculationError(f"Historical basis_pct must be finite Decimal: {val}")

    dec_window = Decimal(window)
    mean = sum(sample) / dec_window

    # Sample variance with Bessel correction (window - 1)
    variance_sum = sum((x - mean) * (x - mean) for x in sample)
    sample_variance = variance_sum / Decimal(window - 1)

    if sample_variance <= Decimal("0"):
        return RollingBasisStats(
            count=window,
            window_size=window,
            rolling_mean=mean,
            rolling_std=Decimal("0"),
            z_score=None,
            z_score_status=BasisZScoreStatus.UNDEFINED,
        )

    sample_std = sample_variance.sqrt()
    if sample_std == Decimal("0"):
        return RollingBasisStats(
            count=window,
            window_size=window,
            rolling_mean=mean,
            rolling_std=Decimal("0"),
            z_score=None,
            z_score_status=BasisZScoreStatus.UNDEFINED,
        )

    current_basis_pct = sample[-1]
    z_score = (current_basis_pct - mean) / sample_std

    if z_score < lower_threshold:
        status = BasisZScoreStatus.LOWER
    elif z_score > upper_threshold:
        status = BasisZScoreStatus.HIGHER
    else:
        status = BasisZScoreStatus.NORMAL

    return RollingBasisStats(
        count=window,
        window_size=window,
        rolling_mean=mean,
        rolling_std=sample_std,
        z_score=z_score,
        z_score_status=status,
    )
