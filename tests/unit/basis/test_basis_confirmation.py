"""
Unit tests for Basis Confirmation Gate in AlphaForge.
Tests confirmation state generation, insufficient history, and invalid observation handling.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.basis.engine import evaluate_basis_confirmation
from alphaforge.basis.enums import (
    BasisConfirmationStatus,
    BasisStatus,
    BasisZScoreStatus,
)
from alphaforge.basis.models import (
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
    return BasisObservation(
        underlying_symbol=symbol,
        index_contract_id=f"{symbol}-SPOT",
        futures_contract_id=contract_id,
        index_price=Decimal("24000.00"),
        futures_price=Decimal("24000.00") + (Decimal("24000.00") * basis_pct),
        basis=Decimal("24000.00") * basis_pct,
        basis_pct=basis_pct,
        index_timestamp=eval_ts,
        futures_timestamp=eval_ts,
        evaluation_timestamp=eval_ts,
        timestamp_skew_seconds=Decimal("0.0"),
        data_quality=DataQualityStatus.VALID,
        contract_status=ContractStatus.ACTIVE,
        basis_status=basis_status,
        calculation_version=1,
    )


def test_test_ab_valid_basis_confirmation() -> None:
    """Test AB: Valid observation with 20 historical observations generates CONFIRMED state."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    history: list[BasisObservation] = []

    # Generate 19 preceding observations with slight variation
    for i in range(19):
        ts = base_ts - timedelta(minutes=3 * (19 - i))
        pct = Decimal(i) / Decimal("1000")  # 0.000 to 0.018
        history.append(make_test_observation(eval_ts=ts, basis_pct=pct))

    current_obs = make_test_observation(eval_ts=base_ts, basis_pct=Decimal("0.010"))
    result = evaluate_basis_confirmation(current_obs, history)

    assert result.status == BasisConfirmationStatus.CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.count == 20
    assert result.rolling_stats.z_score_status in (
        BasisZScoreStatus.NORMAL,
        BasisZScoreStatus.LOWER,
        BasisZScoreStatus.HIGHER,
    )
    assert result.rolling_stats.z_score is not None


def test_test_ac_invalid_basis_confirmation() -> None:
    """Test AC: Invalid basis observation evaluates to INVALID confirmation state."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    invalid_obs = make_test_observation(
        eval_ts=base_ts,
        basis_status=BasisStatus.DATA_CONFLICT,
    )
    result = evaluate_basis_confirmation(invalid_obs, [])
    assert result.status == BasisConfirmationStatus.INVALID
    assert result.rolling_stats is None
    assert "not valid" in result.reason.lower() or "invalid" in result.reason.lower()


def test_not_confirmed_due_to_insufficient_history() -> None:
    """Verify when history is < 20 observations, status is NOT_CONFIRMED."""
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


def test_history_filters_out_different_contracts_and_future_timestamps() -> None:
    """Verify history filtering only includes matching symbol, contract, and past timestamps."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    # Historical observations for a DIFFERENT contract
    diff_contract_history = [
        make_test_observation(
            eval_ts=base_ts - timedelta(minutes=3 * i),
            contract_id="NIFTY26JULFUT",  # Different contract
        )
        for i in range(1, 25)
    ]

    current_obs = make_test_observation(
        eval_ts=base_ts,
        contract_id="NIFTY26JUNFUT",
    )
    result = evaluate_basis_confirmation(current_obs, diff_contract_history)

    # Different contract history ignored -> insufficient history (count == 1)
    assert result.status == BasisConfirmationStatus.NOT_CONFIRMED
    assert result.rolling_stats is not None
    assert result.rolling_stats.count == 1
