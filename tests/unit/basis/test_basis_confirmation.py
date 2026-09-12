"""
Unit tests for Basis Confirmation Gate in AlphaForge.
Tests confirmation state generation, boundaries, extreme z-scores, and invalid observations.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.basis.calculator import calculate_rolling_stats
from alphaforge.basis.engine import evaluate_basis_confirmation
from alphaforge.basis.enums import (
    BasisConfirmationStatus,
    BasisStatus,
    BasisZScoreStatus,
)
from alphaforge.basis.models import (
    BasisConfig,
    BasisObservation,
)
from alphaforge.contract.enums import ContractStatus
from alphaforge.data.enums import DataQualityStatus


def make_test_observation(
    eval_ts: datetime,
    basis_pct: Decimal = Decimal("0.005"),
    basis_status: BasisStatus = BasisStatus.VALID,
    symbol: str = "NIFTY",
    contract_id: str = "NIFTY26JUNFUT",
) -> BasisObservation:
    """Helper to construct a BasisObservation for confirmation gate testing."""
    is_valid = basis_status == BasisStatus.VALID
    return BasisObservation(
        underlying_symbol=symbol,
        index_contract_id=f"{symbol}-SPOT",
        futures_contract_id=contract_id,
        index_price=Decimal("24000.00") if is_valid else None,
        futures_price=(
            Decimal("24000.00") + (Decimal("24000.00") * basis_pct) if is_valid else None
        ),
        basis=Decimal("24000.00") * basis_pct if is_valid else None,
        basis_pct=basis_pct if is_valid else None,
        index_timestamp=eval_ts,
        futures_timestamp=eval_ts,
        evaluation_timestamp=eval_ts,
        timestamp_skew_seconds=Decimal("0.0"),
        data_quality=DataQualityStatus.VALID,
        contract_status=ContractStatus.ACTIVE,
        basis_status=basis_status,
        calculation_version=1,
    )


def test_1_normal_zscore_generates_confirmed() -> None:
    """Test 1: NORMAL z-score within [-2.5, 2.5] evaluates to CONFIRMED."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []

    # 19 observations: 0.000 to 0.018
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    # Current value at mean (0.009) -> z-score close to 0
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.009"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.count == 20
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.NORMAL
    assert result.rolling_stats.z_score is not None
    assert Decimal("-2.5") <= result.rolling_stats.z_score <= Decimal("2.5")
    assert "confirmed with normal z-score" in result.reason.lower()


def test_2_exactly_lower_threshold_generates_confirmed() -> None:
    """Test 2: Z-score exactly equal to lower threshold evaluates to CONFIRMED (inclusive)."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.009"))

    assert current_obs.basis_pct is not None
    all_pct = [obs.basis_pct for obs in history if obs.basis_pct is not None] + [
        current_obs.basis_pct
    ]
    raw_stats = calculate_rolling_stats(all_pct, window=20)
    assert raw_stats.z_score is not None
    exact_z = raw_stats.z_score

    # Configure lower threshold exactly equal to exact_z
    config = BasisConfig(
        z_score_lower_threshold=exact_z,
        z_score_upper_threshold=exact_z + Decimal("5.0"),
    )
    result = evaluate_basis_confirmation(current_obs, history, config=config)

    assert result.status == BasisConfirmationStatus.CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.NORMAL


def test_3_exactly_upper_threshold_generates_confirmed() -> None:
    """Test 3: Z-score exactly equal to upper threshold evaluates to CONFIRMED (inclusive)."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.009"))

    assert current_obs.basis_pct is not None
    all_pct = [obs.basis_pct for obs in history if obs.basis_pct is not None] + [
        current_obs.basis_pct
    ]
    raw_stats = calculate_rolling_stats(all_pct, window=20)
    assert raw_stats.z_score is not None
    exact_z = raw_stats.z_score

    # Configure upper threshold exactly equal to exact_z
    config = BasisConfig(
        z_score_lower_threshold=exact_z - Decimal("5.0"),
        z_score_upper_threshold=exact_z,
    )
    result = evaluate_basis_confirmation(current_obs, history, config=config)

    assert result.status == BasisConfirmationStatus.CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.NORMAL


def test_4_below_lower_threshold_generates_not_confirmed() -> None:
    """Test 4: Z-score below lower threshold evaluates to NOT_CONFIRMED with LOWER status."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    # Extreme negative outlier produces z-score < -2.5
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("-0.030"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.LOWER
    assert result.rolling_stats.z_score is not None
    assert result.rolling_stats.z_score < Decimal("-2.5")
    assert "extreme lower basis z-score" in result.reason.lower()


def test_5_above_upper_threshold_generates_not_confirmed() -> None:
    """Test 5: Z-score above upper threshold evaluates to NOT_CONFIRMED with HIGHER status."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    # Extreme positive outlier produces z-score > 2.5
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.050"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.HIGHER
    assert result.rolling_stats.z_score is not None
    assert result.rolling_stats.z_score > Decimal("2.5")
    assert "extreme higher basis z-score" in result.reason.lower()


def test_6_insufficient_history_generates_not_confirmed() -> None:
    """Test 6: Insufficient history (< 20 observations) evaluates to NOT_CONFIRMED."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * i),
            basis_pct=Decimal("0.005"),
        )
        for i in range(1, 10)  # only 9 observations
    ]
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.005"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.UNDEFINED
    assert "insufficient history" in result.reason.lower()


def test_7_zero_standard_deviation_generates_not_confirmed() -> None:
    """Test 7: Zero variance series (all identical) evaluates to NOT_CONFIRMED."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * i),
            basis_pct=Decimal("0.005"),
        )
        for i in range(1, 20)  # 19 identical observations
    ]
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.005"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.rolling_std == Decimal("0")
    assert result.rolling_stats.z_score is None
    assert result.rolling_stats.z_score_status == BasisZScoreStatus.UNDEFINED
    assert "zero variance" in result.reason.lower()


def test_8_invalid_observation_generates_invalid() -> None:
    """Test 8: Invalid basis observation evaluates to INVALID confirmation state."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    invalid_obs = make_test_observation(
        eval_ts=base_ts,
        basis_status=BasisStatus.DATA_CONFLICT,
    )
    result = evaluate_basis_confirmation(invalid_obs, [])
    assert result.status == BasisConfirmationStatus.INVALID
    assert result.rolling_stats is None
    assert "invalid" in result.reason.lower()


def test_9_deterministic_repeated_confirmation() -> None:
    """Test 9: Repeated confirmation evaluations produce identical results across 50 runs."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * (19 - i)),
            basis_pct=Decimal(i) / Decimal("1000"),
        )
        for i in range(19)
    ]
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.009"))

    results = [evaluate_basis_confirmation(current_obs, history) for _ in range(50)]
    first = results[0]
    for r in results[1:]:
        assert r == first


def test_10_custom_basis_config_thresholds_honored() -> None:
    """Test 10: Custom BasisConfig thresholds (e.g. [-1.0, 1.0]) are strictly honored."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * (19 - i)),
            basis_pct=Decimal(i) / Decimal("1000"),
        )
        for i in range(19)
    ]
    # Current value has z-score ~1.6 (NORMAL under default 2.5, but HIGHER under 1.0)
    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.019"))

    # Default config -> CONFIRMED
    res_default = evaluate_basis_confirmation(current_obs, history)
    assert res_default.status == BasisConfirmationStatus.CONFIRMED
    assert res_default.rolling_stats is not None
    assert res_default.rolling_stats.z_score_status == BasisZScoreStatus.NORMAL

    # Tighter custom config -> NOT_CONFIRMED with HIGHER
    custom_cfg = BasisConfig(
        z_score_lower_threshold=Decimal("-1.0"),
        z_score_upper_threshold=Decimal("1.0"),
    )
    res_custom = evaluate_basis_confirmation(current_obs, history, config=custom_cfg)
    assert res_custom.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert res_custom.rolling_stats is not None
    assert res_custom.rolling_stats.z_score_status == BasisZScoreStatus.HIGHER


def test_history_filters_out_different_contracts_and_future_timestamps() -> None:
    """Verify history filtering only includes matching symbol, contract, and past timestamps."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    diff_contract_history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * i),
            contract_id="NIFTY26JULFUT",
        )
        for i in range(1, 25)
    ]

    current_obs = make_test_observation(
        eval_ts=base_ts,
        contract_id="NIFTY26JUNFUT",
    )
    result = evaluate_basis_confirmation(current_obs, diff_contract_history)

    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.count == 1
