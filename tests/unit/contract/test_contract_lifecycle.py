"""
Unit tests for Contract Lifecycle Engine.
Tests all lifecycle states (A-G), boundary conditions (Q-T), multi-contract resolution (N-O),
rollover candidate detection (X), and pure determinism (P).
"""

from datetime import UTC, datetime, timedelta

import pytest

from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.lifecycle import (
    evaluate_contract_lifecycle,
    get_active_contracts,
    get_current_active_contract,
    get_next_contract,
    get_rollover_candidate,
    is_active,
    is_expired,
    is_expiring,
    is_listed,
    is_tradeable,
)
from alphaforge.core.exceptions import ContractValidationError
from tests.unit.contract.test_contract_models import make_valid_contract


def test_lifecycle_naive_timestamp_rejected() -> None:
    """Verify evaluation timestamp without UTC timezone raises ContractValidationError."""
    c = make_valid_contract()
    naive_ts = datetime(2026, 6, 1, 10, 0, 0)
    with pytest.raises(ContractValidationError, match="explicitly timezone-aware UTC"):
        evaluate_contract_lifecycle(c, naive_ts)


def test_test_a_valid_active_contract() -> None:
    """Test A: Contract within trading window is ACTIVE and tradeable."""
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    status = evaluate_contract_lifecycle(c, eval_ts)
    assert status == ContractStatus.ACTIVE
    assert is_listed(c, eval_ts) is True
    assert is_active(c, eval_ts) is True
    assert is_expiring(c, eval_ts) is False
    assert is_expired(c, eval_ts) is False
    assert is_tradeable(c, eval_ts) is True


def test_test_b_not_yet_listed_contract() -> None:
    """Test B: Contract before listing or trading start is NOT_YET_LISTED and not tradeable."""
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )

    # Before listing
    prior_listing = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c, prior_listing) == ContractStatus.NOT_YET_LISTED
    assert is_listed(c, prior_listing) is False
    assert is_active(c, prior_listing) is False
    assert is_tradeable(c, prior_listing) is False

    # Listed but before trading start
    prior_trading = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c, prior_trading) == ContractStatus.NOT_YET_LISTED
    assert is_listed(c, prior_trading) is True
    assert is_active(c, prior_trading) is False
    assert is_tradeable(c, prior_trading) is False


def test_test_c_expiring_contract() -> None:
    """Test C: Contract within expiring window transitions to EXPIRING."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )

    # 1 hour before expiry (default expiring_window is 2 hours)
    eval_ts = expiry - timedelta(hours=1)
    assert evaluate_contract_lifecycle(c, eval_ts) == ContractStatus.EXPIRING
    assert is_active(c, eval_ts) is False
    assert is_expiring(c, eval_ts) is True
    assert is_expired(c, eval_ts) is False

    # By default, expiring is not tradeable unless explicitly allowed
    assert is_tradeable(c, eval_ts, allow_expiring=False) is False
    assert is_tradeable(c, eval_ts, allow_expiring=True) is True


def test_test_d_expired_contract() -> None:
    """Test D: Contract at or past trading_end or expiry is EXPIRED and non-tradeable."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )

    past_expiry = expiry + timedelta(minutes=1)
    assert evaluate_contract_lifecycle(c, past_expiry) == ContractStatus.EXPIRED
    assert is_active(c, past_expiry) is False
    assert is_expiring(c, past_expiry) is False
    assert is_expired(c, past_expiry) is True
    assert is_tradeable(c, past_expiry) is False


def test_test_e_suspended_contract() -> None:
    """Test E: Contract with is_suspended=True evaluates to SUSPENDED and non-tradeable."""
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        is_suspended=True,
    )
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    assert evaluate_contract_lifecycle(c, eval_ts) == ContractStatus.SUSPENDED
    assert is_active(c, eval_ts) is False
    assert is_tradeable(c, eval_ts) is False


def test_test_p_pure_determinism() -> None:
    """Test P: Lifecycle evaluation is pure and strictly deterministic across repeated calls."""
    c = make_valid_contract()
    eval_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    results = [evaluate_contract_lifecycle(c, eval_ts) for _ in range(50)]
    assert all(r == ContractStatus.ACTIVE for r in results)


def test_test_q_boundary_listing_datetime() -> None:
    """Test Q: Exact boundary timestamp for listing_datetime is inclusive."""
    t_list = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    t_start = datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=t_list,
        trading_start_datetime=t_start,
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )

    # 1 microsecond before listing
    assert (
        evaluate_contract_lifecycle(c, t_list - timedelta(microseconds=1))
        == ContractStatus.NOT_YET_LISTED
    )
    assert is_listed(c, t_list - timedelta(microseconds=1)) is False

    # Exact listing timestamp: listed is True
    assert is_listed(c, t_list) is True
    # Still NOT_YET_LISTED until trading_start
    assert evaluate_contract_lifecycle(c, t_list) == ContractStatus.NOT_YET_LISTED


def test_test_r_boundary_trading_start_datetime() -> None:
    """Test R: Exact boundary timestamp for trading_start_datetime is inclusive."""
    t_start = datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC),
        trading_start_datetime=t_start,
        trading_end_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC),
    )

    # 1 second before trading start: NOT_YET_LISTED
    assert (
        evaluate_contract_lifecycle(c, t_start - timedelta(seconds=1))
        == ContractStatus.NOT_YET_LISTED
    )

    # Exactly at trading_start: ACTIVE
    assert evaluate_contract_lifecycle(c, t_start) == ContractStatus.ACTIVE
    assert is_tradeable(c, t_start) is True


def test_test_s_boundary_trading_end_datetime() -> None:
    """Test S: Exact trading_end_datetime boundary is exclusive (EXPIRED at and after)."""
    t_end = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=t_end,
        expiry_datetime=t_end + timedelta(hours=1),
    )

    # 1 millisecond before trading end: EXPIRING (within window)
    eval_pre = t_end - timedelta(milliseconds=1)
    assert evaluate_contract_lifecycle(c, eval_pre) == ContractStatus.EXPIRING

    # Exactly at trading_end: EXPIRED
    assert evaluate_contract_lifecycle(c, t_end) == ContractStatus.EXPIRED
    assert is_expired(c, t_end) is True
    assert is_tradeable(c, t_end) is False


def test_test_t_boundary_expiry_datetime() -> None:
    """Test T: Exact boundary timestamp for expiry_datetime is exclusive (EXPIRED at and after)."""
    t_end = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    t_exp = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c = make_valid_contract(
        listing_datetime=datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=t_end,
        expiry_datetime=t_exp,
    )

    assert evaluate_contract_lifecycle(c, t_exp) == ContractStatus.EXPIRED
    assert is_expired(c, t_exp) is True
    assert is_tradeable(c, t_exp) is False


def test_test_n_and_o_multi_contract_resolution() -> None:
    """Test N & O: Multi-contract resolution by expiry and active vs next month contracts."""
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
    c_far = make_valid_contract(
        contract_id="NIFTY26AUGFUT",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 8, 27, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 8, 27, 15, 30, 0, tzinfo=UTC),
    )

    # Pass unordered list
    contracts = [c_far, c_near, c_next]

    # Deterministic sorting
    active = get_active_contracts(contracts, base_ts)
    assert len(active) == 3
    assert active[0].contract_id == "NIFTY26JUNFUT"
    assert active[1].contract_id == "NIFTY26JULFUT"
    assert active[2].contract_id == "NIFTY26AUGFUT"

    # Active (front-month) vs next-month contract resolution
    current = get_current_active_contract(contracts, base_ts)
    assert current is not None
    assert current.contract_id == "NIFTY26JUNFUT"

    next_c = get_next_contract(contracts, base_ts)
    assert next_c is not None
    assert next_c.contract_id == "NIFTY26JULFUT"

    # Empty list edge cases
    assert get_current_active_contract([], base_ts) is None
    assert get_next_contract([], base_ts) is None
    assert get_next_contract([c_near], base_ts) is None


def test_test_x_rollover_candidate_detection() -> None:
    """Test X: Rollover candidate pair when front is EXPIRING and next is ACTIVE."""
    jun_expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c_near = make_valid_contract(
        contract_id="NIFTY26JUNFUT",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=jun_expiry,
        expiry_datetime=jun_expiry,
    )
    c_next = make_valid_contract(
        contract_id="NIFTY26JULFUT",
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 7, 30, 15, 30, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 7, 30, 15, 30, 0, tzinfo=UTC),
    )

    contracts = [c_near, c_next]

    # Case 1: 10 days before expiry -> both ACTIVE, no rollover candidate
    normal_ts = jun_expiry - timedelta(days=10)
    assert get_rollover_candidate(contracts, normal_ts) is None

    # Case 2: 1 hour before expiry -> c_near is EXPIRING, c_next is ACTIVE -> Rollover detected!
    expiring_ts = jun_expiry - timedelta(hours=1)
    candidate = get_rollover_candidate(contracts, expiring_ts)
    assert candidate is not None
    front, next_contract = candidate
    assert front.contract_id == "NIFTY26JUNFUT"
    assert next_contract.contract_id == "NIFTY26JULFUT"

    # Case 3: Post expiry -> c_near is EXPIRED -> no rollover pair
    post_ts = jun_expiry + timedelta(minutes=5)
    assert get_rollover_candidate(contracts, post_ts) is None

    # Case 4: Only 1 contract in universe -> no rollover candidate
    assert get_rollover_candidate([c_near], expiring_ts) is None


def test_invalid_declared_status_never_becomes_active_or_expiring() -> None:
    """Tests 1 & 2 & 9: Declared status INVALID must never evaluate to ACTIVE or EXPIRING."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c_invalid = make_valid_contract(
        status=ContractStatus.INVALID,
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )
    # Active window timestamp
    active_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c_invalid, active_ts) == ContractStatus.INVALID
    assert is_active(c_invalid, active_ts) is False
    assert is_expiring(c_invalid, active_ts) is False
    assert is_tradeable(c_invalid, active_ts) is False

    # Expiring window timestamp (1 hour before expiry)
    expiring_ts = expiry - timedelta(hours=1)
    assert evaluate_contract_lifecycle(c_invalid, expiring_ts) == ContractStatus.INVALID
    assert is_active(c_invalid, expiring_ts) is False
    assert is_expiring(c_invalid, expiring_ts) is False
    assert is_tradeable(c_invalid, expiring_ts, allow_expiring=True) is False


def test_unknown_declared_status_never_becomes_active_or_expiring() -> None:
    """Tests 3 & 4 & 10: Declared status UNKNOWN must never evaluate to ACTIVE or EXPIRING."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c_unknown = make_valid_contract(
        status=ContractStatus.UNKNOWN,
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )
    # Active window timestamp
    active_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c_unknown, active_ts) == ContractStatus.UNKNOWN
    assert is_active(c_unknown, active_ts) is False
    assert is_expiring(c_unknown, active_ts) is False
    assert is_tradeable(c_unknown, active_ts) is False

    # Expiring window timestamp (1 hour before expiry)
    expiring_ts = expiry - timedelta(hours=1)
    assert evaluate_contract_lifecycle(c_unknown, expiring_ts) == ContractStatus.UNKNOWN
    assert is_active(c_unknown, expiring_ts) is False
    assert is_expiring(c_unknown, expiring_ts) is False
    assert is_tradeable(c_unknown, expiring_ts, allow_expiring=True) is False


def test_suspended_declared_status_remains_nontradeable() -> None:
    """Test 5: Declared status SUSPENDED remains non-tradeable regardless of timestamps."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c_suspended = make_valid_contract(
        status=ContractStatus.SUSPENDED,
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )
    active_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c_suspended, active_ts) == ContractStatus.SUSPENDED
    assert is_active(c_suspended, active_ts) is False
    assert is_tradeable(c_suspended, active_ts) is False


def test_expired_declared_status_remains_expired() -> None:
    """Test 8: Declared status EXPIRED remains EXPIRED even if evaluated prior to expiry."""
    expiry = datetime(2026, 6, 25, 15, 30, 0, tzinfo=UTC)
    c_expired = make_valid_contract(
        status=ContractStatus.EXPIRED,
        listing_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 1, 1, 9, 15, 0, tzinfo=UTC),
        trading_end_datetime=expiry,
        expiry_datetime=expiry,
    )
    # Active window timestamp
    active_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert evaluate_contract_lifecycle(c_expired, active_ts) == ContractStatus.EXPIRED
    assert is_active(c_expired, active_ts) is False
    assert is_expired(c_expired, active_ts) is True
    assert is_tradeable(c_expired, active_ts) is False


def test_declared_status_fail_closed_determinism() -> None:
    """Test 11: Repeated evaluations of declared invalid/unknown are strictly deterministic."""
    c_inv = make_valid_contract(status=ContractStatus.INVALID)
    c_unk = make_valid_contract(status=ContractStatus.UNKNOWN)
    active_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)

    inv_results = [evaluate_contract_lifecycle(c_inv, active_ts) for _ in range(50)]
    assert all(r == ContractStatus.INVALID for r in inv_results)

    unk_results = [evaluate_contract_lifecycle(c_unk, active_ts) for _ in range(50)]
    assert all(r == ContractStatus.UNKNOWN for r in unk_results)
