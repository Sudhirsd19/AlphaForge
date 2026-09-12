"""
Unit tests for Index-Futures Basis Engine.
Tests contract matching, data quality, lifecycle gates, timestamp alignment, and determinism.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.basis.engine import evaluate_basis
from alphaforge.basis.enums import BasisStatus
from alphaforge.basis.models import BasisConfig, BasisObservation
from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.lifecycle import get_current_active_contract
from alphaforge.core.exceptions import BasisCalculationError
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from tests.unit.contract.test_contract_models import make_valid_contract


def make_test_candle(
    symbol: str = "NIFTY",
    contract_id: str = "NIFTY-SPOT",
    instrument_type: InstrumentType = InstrumentType.INDEX,
    timestamp: datetime | None = None,
    price: Decimal = Decimal("24000.00"),
    quality_status: DataQualityStatus = DataQualityStatus.VALID,
) -> MarketCandle:
    """Helper to construct a valid MarketCandle for basis testing."""
    ts = timestamp or datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    return MarketCandle(
        symbol=symbol,
        instrument_type=instrument_type,
        contract_id=contract_id,
        exchange_timestamp=ts,
        received_timestamp=ts,
        timeframe="3m",
        open=price,
        high=price + Decimal("10.00"),
        low=price - Decimal("10.00"),
        close=price,
        volume=1000,
        quality_status=quality_status,
        data_version=1,
        source="TEST_FEED",
        is_closed=True,
    )


def test_test_f_valid_active_basis_observation() -> None:
    """Test F: Valid index, active futures, and matching timestamps evaluate to VALID."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    idx_candle = make_test_candle(
        symbol="NIFTY",
        contract_id="NIFTY-SPOT",
        instrument_type=InstrumentType.INDEX,
        timestamp=eval_ts,
        price=Decimal("24000.00"),
    )
    fut_candle = make_test_candle(
        symbol="NIFTY",
        contract_id="NIFTY26JUNFUT",
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
        price=Decimal("24060.00"),
    )
    contract = make_valid_contract(
        contract_id="NIFTY26JUNFUT",
        underlying_symbol="NIFTY",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )

    obs = evaluate_basis(idx_candle, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.VALID
    assert obs.basis == Decimal("60.00")
    # 60 / 24000 = 0.0025
    assert obs.basis_pct == Decimal("0.0025")
    assert obs.timestamp_skew_seconds == Decimal("0.0")
    assert obs.contract_status == ContractStatus.ACTIVE
    assert obs.data_quality == DataQualityStatus.VALID


def test_test_e_mismatched_underlying_rejected() -> None:
    """Test E: Mismatched underlying symbol evaluates to UNDERLYING_MISMATCH."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    idx_candle = make_test_candle(symbol="NIFTY", contract_id="NIFTY-SPOT", timestamp=eval_ts)
    fut_candle = make_test_candle(
        symbol="BANKNIFTY",
        contract_id="BANKNIFTY26JUNFUT",
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )
    contract = make_valid_contract(
        contract_id="BANKNIFTY26JUNFUT",
        underlying_symbol="BANKNIFTY",
    )

    obs = evaluate_basis(idx_candle, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.UNDERLYING_MISMATCH


def test_test_g_expired_futures_rejected() -> None:
    """Test G: Futures contract that has expired evaluates to CONTRACT_EXPIRED."""
    expiry = datetime(2026, 5, 28, 15, 30, 0, tzinfo=UTC)
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)  # post expiry

    contract = make_valid_contract(
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )
    idx_candle = make_test_candle(timestamp=eval_ts)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_candle, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.CONTRACT_EXPIRED


def test_test_h_suspended_futures_rejected() -> None:
    """Test H: Futures contract marked suspended evaluates to CONTRACT_SUSPENDED."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract(is_suspended=True)

    idx_candle = make_test_candle(timestamp=eval_ts)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_candle, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.CONTRACT_SUSPENDED


def test_test_i_invalid_futures_contract_rejected() -> None:
    """Test I: Futures contract with status INVALID or UNKNOWN evaluates to CONTRACT_INVALID."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract_inv = make_valid_contract(status=ContractStatus.INVALID)
    contract_unk = make_valid_contract(status=ContractStatus.UNKNOWN)

    idx_candle = make_test_candle(timestamp=eval_ts)
    fut_candle = make_test_candle(
        contract_id=contract_inv.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs_inv = evaluate_basis(idx_candle, fut_candle, contract_inv, eval_ts)
    assert obs_inv.basis_status == BasisStatus.CONTRACT_INVALID

    obs_unk = evaluate_basis(idx_candle, fut_candle, contract_unk, eval_ts)
    assert obs_unk.basis_status == BasisStatus.CONTRACT_INVALID


def test_test_m_n_future_dated_observations_rejected() -> None:
    """Tests M & N: Future-dated index or futures observations evaluate to FUTURE_DATED_DATA."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    # Index future-dated
    idx_future = make_test_candle(timestamp=eval_ts + timedelta(minutes=1))
    fut_normal = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )
    obs_m = evaluate_basis(idx_future, fut_normal, contract, eval_ts)
    assert obs_m.basis_status == BasisStatus.FUTURE_DATED_DATA

    # Futures future-dated
    idx_normal = make_test_candle(timestamp=eval_ts)
    fut_future = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts + timedelta(minutes=1),
    )
    obs_n = evaluate_basis(idx_normal, fut_future, contract, eval_ts)
    assert obs_n.basis_status == BasisStatus.FUTURE_DATED_DATA


def test_test_o_stale_observation_rejected() -> None:
    """Test O: Observation older than max_stale_seconds evaluates to STALE."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()
    config = BasisConfig(max_stale_seconds=Decimal("300"))

    # 301 seconds old
    old_ts = eval_ts - timedelta(seconds=301)
    idx_stale = make_test_candle(timestamp=old_ts)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_stale, fut_candle, contract, eval_ts, config=config)
    assert obs.basis_status == BasisStatus.STALE


def test_test_p_gap_rejected() -> None:
    """Test P: Input data with DataQualityStatus.GAP evaluates to DATA_GAP."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    idx_gap = make_test_candle(timestamp=eval_ts, quality_status=DataQualityStatus.GAP)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_gap, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.DATA_GAP


def test_test_q_conflict_rejected() -> None:
    """Test Q: Input data with DataQualityStatus.CONFLICT evaluates to DATA_CONFLICT."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    idx_conflict = make_test_candle(timestamp=eval_ts, quality_status=DataQualityStatus.CONFLICT)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_conflict, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.DATA_CONFLICT


def test_test_r_duplicate_safely_handled() -> None:
    """Test R: Input data with DataQualityStatus.DUPLICATE evaluates to VALID basis."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    idx_dup = make_test_candle(timestamp=eval_ts, quality_status=DataQualityStatus.DUPLICATE)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_dup, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.VALID
    assert obs.data_quality == DataQualityStatus.DUPLICATE


def test_test_s_out_of_order_safely_normalized() -> None:
    """Test S: Input data with DataQualityStatus.OUT_OF_ORDER evaluates to VALID basis."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    idx_ooo = make_test_candle(timestamp=eval_ts, quality_status=DataQualityStatus.OUT_OF_ORDER)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    obs = evaluate_basis(idx_ooo, fut_candle, contract, eval_ts)
    assert obs.basis_status == BasisStatus.VALID
    assert obs.data_quality == DataQualityStatus.OUT_OF_ORDER


def test_test_j_k_l_engine_timestamp_skew() -> None:
    """Tests J, K, L in engine: Skew within, at, and beyond max_timestamp_skew_seconds."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()
    config = BasisConfig(max_timestamp_skew_seconds=Decimal("60"))

    # Test J: 30s skew -> VALID
    idx_30 = make_test_candle(timestamp=eval_ts - timedelta(seconds=30))
    fut_now = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )
    obs_j = evaluate_basis(idx_30, fut_now, contract, eval_ts, config=config)
    assert obs_j.basis_status == BasisStatus.VALID
    assert obs_j.timestamp_skew_seconds == Decimal("30.0")

    # Test K: 60s skew -> VALID
    idx_60 = make_test_candle(timestamp=eval_ts - timedelta(seconds=60))
    obs_k = evaluate_basis(idx_60, fut_now, contract, eval_ts, config=config)
    assert obs_k.basis_status == BasisStatus.VALID
    assert obs_k.timestamp_skew_seconds == Decimal("60.0")

    # Test L: 61s skew -> MISALIGNED_TIMESTAMP
    idx_61 = make_test_candle(timestamp=eval_ts - timedelta(seconds=61))
    obs_l = evaluate_basis(idx_61, fut_now, contract, eval_ts, config=config)
    assert obs_l.basis_status == BasisStatus.MISALIGNED_TIMESTAMP
    assert obs_l.timestamp_skew_seconds == Decimal("61.0")


def test_test_z_deterministic_repeated_evaluation() -> None:
    """Test Z: Repeated evaluation produces identical BasisObservation results across 50 runs."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()
    idx_candle = make_test_candle(timestamp=eval_ts)
    fut_candle = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    results = [evaluate_basis(idx_candle, fut_candle, contract, eval_ts) for _ in range(50)]
    first = results[0]
    for r in results[1:]:
        assert r == first


def test_test_aa_deterministic_contract_selection() -> None:
    """Test AA: Contract selection using Phase 3 get_current_active_contract for basis pairing."""
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    c_near = make_valid_contract(
        contract_id="NIFTY26JUNFUT",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )
    c_next = make_valid_contract(
        contract_id="NIFTY26JULFUT",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 7, 30, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 7, 30, 15, 30, 0, tzinfo=UTC),
    )

    contracts = [c_next, c_near]  # Unordered
    active_front = get_current_active_contract(contracts, base_ts)
    assert active_front is not None
    assert active_front.contract_id == "NIFTY26JUNFUT"

    # Evaluate basis using selected active front contract
    idx = make_test_candle(timestamp=base_ts)
    fut = make_test_candle(
        contract_id=active_front.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=base_ts,
    )
    obs = evaluate_basis(idx, fut, active_front, base_ts)
    assert obs.basis_status == BasisStatus.VALID
    assert obs.futures_contract_id == "NIFTY26JUNFUT"


def test_invalid_prices_never_create_numeric_placeholders() -> None:
    """Verify non-positive prices result in None and no fabricated prices (1 or 0)."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()

    # Zero index price simulated via model_construct
    idx_zero = MarketCandle.model_construct(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=eval_ts,
        received_timestamp=eval_ts,
        timeframe="3m",
        open=Decimal("24000.00"),
        high=Decimal("24010.00"),
        low=Decimal("23990.00"),
        close=Decimal("0"),
        volume=1000,
        quality_status=DataQualityStatus.VALID,
        data_version=1,
        source="TEST_FEED",
        is_closed=True,
    )
    fut_valid = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
        price=Decimal("24000.00"),
    )
    obs_idx_zero = evaluate_basis(idx_zero, fut_valid, contract, eval_ts)

    assert obs_idx_zero.basis_status == BasisStatus.INVALID
    assert obs_idx_zero.index_price is None
    assert obs_idx_zero.basis is None
    assert obs_idx_zero.basis_pct is None
    assert obs_idx_zero.index_price != Decimal("1")
    assert obs_idx_zero.basis != Decimal("0")
    # Valid futures price is preserved for auditability
    assert obs_idx_zero.futures_price == Decimal("24000.00")

    # Negative futures price simulated via model_construct
    idx_valid = make_test_candle(
        contract_id="NIFTY-SPOT",
        instrument_type=InstrumentType.INDEX,
        timestamp=eval_ts,
        price=Decimal("24000.00"),
    )
    fut_neg = MarketCandle.model_construct(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id=contract.contract_id,
        exchange_timestamp=eval_ts,
        received_timestamp=eval_ts,
        timeframe="3m",
        open=Decimal("24000.00"),
        high=Decimal("24010.00"),
        low=Decimal("23990.00"),
        close=Decimal("-24000.00"),
        volume=1000,
        quality_status=DataQualityStatus.VALID,
        data_version=1,
        source="TEST_FEED",
        is_closed=True,
    )
    obs_fut_neg = evaluate_basis(idx_valid, fut_neg, contract, eval_ts)

    assert obs_fut_neg.basis_status == BasisStatus.INVALID
    assert obs_fut_neg.futures_price is None
    assert obs_fut_neg.basis is None
    assert obs_fut_neg.basis_pct is None
    assert obs_fut_neg.futures_price != Decimal("1")
    assert obs_fut_neg.basis != Decimal("0")
    assert obs_fut_neg.index_price == Decimal("24000.00")


def test_defective_observations_never_report_zero_basis() -> None:
    """Verify defective observations have basis=None and never fabricate basis=0."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    contract = make_valid_contract()
    fut_valid = make_test_candle(
        contract_id=contract.contract_id,
        instrument_type=InstrumentType.FUTURES,
        timestamp=eval_ts,
    )

    # 1. Stale candle
    old_ts = eval_ts - timedelta(seconds=301)
    idx_stale = make_test_candle(timestamp=old_ts)
    obs_stale = evaluate_basis(idx_stale, fut_valid, contract, eval_ts)
    assert obs_stale.basis_status == BasisStatus.STALE
    assert obs_stale.basis is None
    assert obs_stale.basis_pct is None
    assert obs_stale.basis != Decimal("0")

    # 2. Misaligned timestamp
    idx_skew = make_test_candle(timestamp=eval_ts - timedelta(seconds=65))
    obs_skew = evaluate_basis(idx_skew, fut_valid, contract, eval_ts)
    assert obs_skew.basis_status == BasisStatus.MISALIGNED_TIMESTAMP
    assert obs_skew.basis is None
    assert obs_skew.basis_pct is None
    assert obs_skew.basis != Decimal("0")

    # 3. Gap candle
    idx_gap = make_test_candle(timestamp=eval_ts, quality_status=DataQualityStatus.GAP)
    obs_gap = evaluate_basis(idx_gap, fut_valid, contract, eval_ts)
    assert obs_gap.basis_status == BasisStatus.DATA_GAP
    assert obs_gap.basis is None
    assert obs_gap.basis_pct is None
    assert obs_gap.basis != Decimal("0")


def test_model_validation_enforces_none_basis_on_invalid_observation() -> None:
    """Verify BasisObservation model rejects numeric basis on non-VALID status."""
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    # Creating an invalid observation with basis != None must raise BasisCalculationError
    with pytest.raises(BasisCalculationError, match="must have None for basis and basis_pct"):
        BasisObservation(
            underlying_symbol="NIFTY",
            index_contract_id="NIFTY-SPOT",
            futures_contract_id="NIFTY26JUNFUT",
            index_price=Decimal("24000.00"),
            futures_price=Decimal("24050.00"),
            basis=Decimal("0"),
            basis_pct=Decimal("0"),
            index_timestamp=eval_ts,
            futures_timestamp=eval_ts,
            evaluation_timestamp=eval_ts,
            timestamp_skew_seconds=Decimal("0.0"),
            data_quality=DataQualityStatus.VALID,
            contract_status=ContractStatus.ACTIVE,
            basis_status=BasisStatus.INVALID,
            calculation_version=1,
        )
