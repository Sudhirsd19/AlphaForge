"""
AlphaForge Index-Futures Basis Engine.
Coordinates market data observations, contract lifecycle gates, timestamp alignment,
and pure deterministic confirmation evaluation.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.basis.calculator import (
    calculate_basis,
    calculate_basis_pct,
    calculate_rolling_stats,
    calculate_timestamp_skew,
)
from alphaforge.basis.enums import (
    BasisConfirmationStatus,
    BasisStatus,
    BasisZScoreStatus,
)
from alphaforge.basis.models import (
    BasisConfig,
    BasisConfirmationResult,
    BasisObservation,
)
from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.lifecycle import evaluate_contract_lifecycle
from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import BasisCalculationError
from alphaforge.data.enums import DataQualityStatus
from alphaforge.data.models import MarketCandle


def evaluate_basis(
    index_candle: MarketCandle,
    futures_candle: MarketCandle,
    futures_contract: ContractMaster,
    evaluation_timestamp: datetime,
    config: BasisConfig | None = None,
) -> BasisObservation:
    """
    Pure mathematical evaluation of index-futures basis at evaluation_timestamp.
    Fails closed on timestamp anomalies, data defects, contract lifecycle status,
    or symbol mismatch.
    """
    cfg = config or BasisConfig()
    if evaluation_timestamp.tzinfo is None or evaluation_timestamp.utcoffset() != UTC.utcoffset(
        evaluation_timestamp
    ):
        raise BasisCalculationError(
            f"evaluation_timestamp must be explicitly timezone-aware UTC: {evaluation_timestamp}"
        )

    # 1. Temporal causality check: no future-dated observations relative to evaluation time
    if (
        index_candle.exchange_timestamp > evaluation_timestamp
        or futures_candle.exchange_timestamp > evaluation_timestamp
    ):
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.FUTURE_DATED_DATA,
            config=cfg,
        )

    # 2. Stale data check: observation age relative to evaluation_timestamp
    index_age = (evaluation_timestamp - index_candle.exchange_timestamp).total_seconds()
    futures_age = (evaluation_timestamp - futures_candle.exchange_timestamp).total_seconds()
    if (
        Decimal(str(index_age)) > cfg.max_stale_seconds
        or Decimal(str(futures_age)) > cfg.max_stale_seconds
    ):
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.STALE,
            config=cfg,
        )

    # 3. Data quality checks (Phase 2 contract safety)
    q_index = index_candle.quality_status
    q_futures = futures_candle.quality_status
    if q_index == DataQualityStatus.CONFLICT or q_futures == DataQualityStatus.CONFLICT:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.DATA_CONFLICT,
            config=cfg,
        )

    if q_index == DataQualityStatus.GAP or q_futures == DataQualityStatus.GAP:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.DATA_GAP,
            config=cfg,
        )

    if q_index == DataQualityStatus.STALE or q_futures == DataQualityStatus.STALE:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.STALE,
            config=cfg,
        )

    if q_index in (DataQualityStatus.INVALID, DataQualityStatus.INCOMPLETE) or q_futures in (
        DataQualityStatus.INVALID,
        DataQualityStatus.INCOMPLETE,
    ):
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.INVALID,
            config=cfg,
        )

    # 4. Underlying consistency check
    norm_index_sym = index_candle.symbol.strip().upper()
    norm_fut_sym = futures_candle.symbol.strip().upper()
    norm_contract_sym = futures_contract.underlying_symbol.strip().upper()
    if norm_index_sym != norm_fut_sym or norm_fut_sym != norm_contract_sym:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.UNDERLYING_MISMATCH,
            config=cfg,
        )

    # 5. Contract identifier matching
    if futures_candle.contract_id.strip().upper() != futures_contract.contract_id.strip().upper():
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=futures_contract.status,
            basis_status=BasisStatus.CONTRACT_INVALID,
            config=cfg,
        )

    # 6. Contract lifecycle evaluation (Phase 3 integration)
    lifecycle_st = evaluate_contract_lifecycle(futures_contract, evaluation_timestamp)
    if lifecycle_st == ContractStatus.INVALID or lifecycle_st == ContractStatus.UNKNOWN:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.CONTRACT_INVALID,
            config=cfg,
        )

    if lifecycle_st == ContractStatus.NOT_YET_LISTED:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.CONTRACT_NOT_LISTED,
            config=cfg,
        )

    if lifecycle_st == ContractStatus.EXPIRED:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.CONTRACT_EXPIRED,
            config=cfg,
        )

    if lifecycle_st == ContractStatus.SUSPENDED:
        skew = calculate_timestamp_skew(
            index_candle.exchange_timestamp, futures_candle.exchange_timestamp
        )
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.CONTRACT_SUSPENDED,
            config=cfg,
        )

    # 7. Timestamp skew check
    skew = calculate_timestamp_skew(
        index_candle.exchange_timestamp, futures_candle.exchange_timestamp
    )
    if skew > cfg.max_timestamp_skew_seconds:
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.MISALIGNED_TIMESTAMP,
            config=cfg,
        )

    # 8. Numeric boundary checks
    if index_candle.close <= Decimal("0") or futures_candle.close <= Decimal("0"):
        return _make_fallback_observation(
            index_candle=index_candle,
            futures_candle=futures_candle,
            evaluation_timestamp=evaluation_timestamp,
            skew=skew,
            contract_status=lifecycle_st,
            basis_status=BasisStatus.INVALID,
            config=cfg,
        )

    # 9. Valid basis calculation
    basis = calculate_basis(index_candle.close, futures_candle.close)
    basis_pct = calculate_basis_pct(index_candle.close, futures_candle.close)

    # Determine aggregate data quality
    data_quality = (
        DataQualityStatus.DUPLICATE
        if (
            index_candle.quality_status == DataQualityStatus.DUPLICATE
            or futures_candle.quality_status == DataQualityStatus.DUPLICATE
        )
        else DataQualityStatus.OUT_OF_ORDER
        if (
            index_candle.quality_status == DataQualityStatus.OUT_OF_ORDER
            or futures_candle.quality_status == DataQualityStatus.OUT_OF_ORDER
        )
        else DataQualityStatus.VALID
    )

    return BasisObservation(
        underlying_symbol=norm_index_sym,
        index_contract_id=index_candle.contract_id.strip().upper(),
        futures_contract_id=futures_candle.contract_id.strip().upper(),
        index_price=index_candle.close,
        futures_price=futures_candle.close,
        basis=basis,
        basis_pct=basis_pct,
        index_timestamp=index_candle.exchange_timestamp,
        futures_timestamp=futures_candle.exchange_timestamp,
        evaluation_timestamp=evaluation_timestamp,
        timestamp_skew_seconds=skew,
        data_quality=data_quality,
        contract_status=lifecycle_st,
        basis_status=BasisStatus.VALID,
        calculation_version=cfg.calculation_version,
    )


def evaluate_basis_confirmation(
    observation: BasisObservation,
    historical_observations: Sequence[BasisObservation],
    config: BasisConfig | None = None,
) -> BasisConfirmationResult:
    """
    Pure deterministic basis confirmation gate.
    Evaluates rolling z-score against baseline window and thresholds.
    """
    cfg = config or BasisConfig()
    if observation.basis_status != BasisStatus.VALID:
        return BasisConfirmationResult(
            status=BasisConfirmationStatus.INVALID,
            observation=observation,
            rolling_stats=None,
            reason=f"Current basis observation is invalid: {observation.basis_status}",
        )

    # Extract valid historical basis_pct series for same contract pair
    history_pct: list[Decimal] = []
    for obs in historical_observations:
        if (
            obs.basis_status == BasisStatus.VALID
            and obs.underlying_symbol == observation.underlying_symbol
            and obs.futures_contract_id == observation.futures_contract_id
            and obs.evaluation_timestamp < observation.evaluation_timestamp
        ):
            history_pct.append(obs.basis_pct)

    # Append current observation to history
    history_pct.append(observation.basis_pct)

    stats = calculate_rolling_stats(
        history_pct,
        window=cfg.rolling_window_size,
        lower_threshold=cfg.z_score_lower_threshold,
        upper_threshold=cfg.z_score_upper_threshold,
    )

    if stats.z_score_status == BasisZScoreStatus.UNDEFINED:
        return BasisConfirmationResult(
            status=BasisConfirmationStatus.NOT_CONFIRMED,
            observation=observation,
            rolling_stats=stats,
            reason="Insufficient history or zero variance for basis z-score confirmation",
        )

    return BasisConfirmationResult(
        status=BasisConfirmationStatus.CONFIRMED,
        observation=observation,
        rolling_stats=stats,
        reason=(
            f"Basis confirmed with z-score {stats.z_score:.4f} "
            f"({stats.z_score_status}) over {stats.count} observations"
            if stats.z_score is not None
            else "Basis confirmed"
        ),
    )


def _make_fallback_observation(
    index_candle: MarketCandle,
    futures_candle: MarketCandle,
    evaluation_timestamp: datetime,
    skew: Decimal,
    contract_status: ContractStatus,
    basis_status: BasisStatus,
    config: BasisConfig,
) -> BasisObservation:
    """Helper to construct fail-closed BasisObservation when defects prevent calculation."""
    # Compute basis values if prices are positive and finite, otherwise 0
    safe_idx = (
        index_candle.close
        if (index_candle.close.is_finite() and index_candle.close > Decimal("0"))
        else Decimal("1")
    )
    safe_fut = (
        futures_candle.close
        if (futures_candle.close.is_finite() and futures_candle.close > Decimal("0"))
        else Decimal("1")
    )

    # If prices were invalid, basis will default to 0
    if index_candle.close > Decimal("0") and futures_candle.close > Decimal("0"):
        basis = futures_candle.close - index_candle.close
        basis_pct = basis / index_candle.close
    else:
        basis = Decimal("0")
        basis_pct = Decimal("0")

    agg_quality = (
        index_candle.quality_status
        if index_candle.quality_status != DataQualityStatus.VALID
        else futures_candle.quality_status
    )

    return BasisObservation(
        underlying_symbol=index_candle.symbol.strip().upper(),
        index_contract_id=index_candle.contract_id.strip().upper(),
        futures_contract_id=futures_candle.contract_id.strip().upper(),
        index_price=safe_idx,
        futures_price=safe_fut,
        basis=basis,
        basis_pct=basis_pct,
        index_timestamp=index_candle.exchange_timestamp,
        futures_timestamp=futures_candle.exchange_timestamp,
        evaluation_timestamp=evaluation_timestamp,
        timestamp_skew_seconds=skew,
        data_quality=agg_quality,
        contract_status=contract_status,
        basis_status=basis_status,
        calculation_version=config.calculation_version,
    )
