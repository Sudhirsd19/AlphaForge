"""
AlphaForge Futures Contract Lifecycle Engine.
Provides deterministic, pure evaluation of contract lifecycle states, expiry boundaries,
tradeability, and rollover candidate eligibility without side effects.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import ContractValidationError


def evaluate_contract_lifecycle(
    contract: ContractMaster,
    evaluation_timestamp: datetime,
    expiring_window: timedelta = timedelta(hours=2),
) -> ContractStatus:
    """
    Pure mathematical evaluation of contract lifecycle state at evaluation_timestamp.

    Precedence and Fail-Closed Governance:
      1. Declared contract status INVALID/UNKNOWN/SUSPENDED is fail-closed and takes
         precedence over timestamp-derived lifecycle evaluation.
      2. If contract.status == ContractStatus.INVALID: returns INVALID.
      3. If contract.status == ContractStatus.UNKNOWN: returns UNKNOWN.
      4. If contract.status == ContractStatus.SUSPENDED or contract.is_suspended: returns SUSPENDED.
      5. If contract.status == ContractStatus.EXPIRED: returns EXPIRED.
      6. Otherwise evaluate deterministic timestamp lifecycle rules:
         - evaluation_timestamp < listing_datetime: NOT_YET_LISTED
         - listing_datetime <= evaluation_timestamp < trading_start_datetime: NOT_YET_LISTED
         - trading_start_datetime <= evaluation_timestamp < trading_end_datetime:
             * if (expiry_datetime - evaluation_timestamp) <= expiring_window: EXPIRING
             * else: ACTIVE
         - evaluation_timestamp >= trading_end_datetime OR
           evaluation_timestamp >= expiry_datetime: EXPIRED

    Boundary Rules:
      - listing_datetime: Inclusive for listing
      - trading_start_datetime: Inclusive for active trading
      - trading_end_datetime: Exclusive for active trading (EXPIRED at and after)
      - expiry_datetime: Exclusive for active trading (EXPIRED at and after)
    """
    if evaluation_timestamp.tzinfo is None or evaluation_timestamp.utcoffset() != UTC.utcoffset(
        evaluation_timestamp
    ):
        raise ContractValidationError(
            f"evaluation_timestamp must be explicitly timezone-aware UTC: {evaluation_timestamp}"
        )

    # 1. Declared status fail-closed precedence
    if contract.status == ContractStatus.INVALID:
        return ContractStatus.INVALID

    if contract.status == ContractStatus.UNKNOWN:
        return ContractStatus.UNKNOWN

    if contract.status == ContractStatus.SUSPENDED or contract.is_suspended:
        return ContractStatus.SUSPENDED

    if contract.status == ContractStatus.EXPIRED:
        return ContractStatus.EXPIRED

    # 2. Prior to listing
    if evaluation_timestamp < contract.listing_datetime:
        return ContractStatus.NOT_YET_LISTED

    # 3. Listed but prior to trading start
    if evaluation_timestamp < contract.trading_start_datetime:
        return ContractStatus.NOT_YET_LISTED

    # 4. Post trading end or post expiry
    if (
        evaluation_timestamp >= contract.trading_end_datetime
        or evaluation_timestamp >= contract.expiry_datetime
    ):
        return ContractStatus.EXPIRED

    # 5. Check if within expiring window prior to expiry
    time_to_expiry = contract.expiry_datetime - evaluation_timestamp
    if time_to_expiry <= expiring_window:
        return ContractStatus.EXPIRING

    return ContractStatus.ACTIVE


def is_listed(contract: ContractMaster, evaluation_timestamp: datetime) -> bool:
    """True if evaluation_timestamp is at or after contract listing_datetime."""
    return evaluation_timestamp >= contract.listing_datetime


def is_active(contract: ContractMaster, evaluation_timestamp: datetime) -> bool:
    """True if contract evaluated status is ACTIVE."""
    return evaluate_contract_lifecycle(contract, evaluation_timestamp) == ContractStatus.ACTIVE


def is_expiring(contract: ContractMaster, evaluation_timestamp: datetime) -> bool:
    """True if contract evaluated status is EXPIRING."""
    return evaluate_contract_lifecycle(contract, evaluation_timestamp) == ContractStatus.EXPIRING


def is_expired(contract: ContractMaster, evaluation_timestamp: datetime) -> bool:
    """True if contract evaluated status is EXPIRED."""
    return evaluate_contract_lifecycle(contract, evaluation_timestamp) == ContractStatus.EXPIRED


def is_tradeable(
    contract: ContractMaster,
    evaluation_timestamp: datetime,
    allow_expiring: bool = False,
) -> bool:
    """
    Determine if contract is eligible for execution/trading.

    Strict Fail-Closed Invariants:
      - Returns False for UNKNOWN, NOT_YET_LISTED, EXPIRED, SUSPENDED, INVALID.
      - Returns True ONLY for explicitly ACTIVE contracts
        (and optionally EXPIRING if allow_expiring=True).
    """
    status = evaluate_contract_lifecycle(contract, evaluation_timestamp)
    if status == ContractStatus.ACTIVE:
        return True
    return bool(allow_expiring and status == ContractStatus.EXPIRING)


def get_active_contracts(
    contracts: Sequence[ContractMaster],
    evaluation_timestamp: datetime,
    include_expiring: bool = True,
) -> list[ContractMaster]:
    """
    Return all tradeable active contracts for an underlying,
    sorted deterministically by expiry_datetime ascending.
    """
    eligible: list[ContractMaster] = []
    for c in contracts:
        st = evaluate_contract_lifecycle(c, evaluation_timestamp)
        if st == ContractStatus.ACTIVE or (include_expiring and st == ContractStatus.EXPIRING):
            eligible.append(c)

    # Sort deterministically by expiry_datetime ascending, then contract_id
    return sorted(eligible, key=lambda c: (c.expiry_datetime, c.contract_id))


def get_current_active_contract(
    contracts: Sequence[ContractMaster],
    evaluation_timestamp: datetime,
    include_expiring: bool = True,
) -> ContractMaster | None:
    """
    Return the front-month (nearest expiry) active contract, or None if no active contracts exist.
    """
    active_list = get_active_contracts(
        contracts, evaluation_timestamp, include_expiring=include_expiring
    )
    if not active_list:
        return None
    return active_list[0]


def get_next_contract(
    contracts: Sequence[ContractMaster],
    evaluation_timestamp: datetime,
) -> ContractMaster | None:
    """
    Return the next-month (second nearest expiry) active contract, or None if unavailable.
    """
    active_list = get_active_contracts(contracts, evaluation_timestamp, include_expiring=True)
    if len(active_list) < 2:
        return None
    return active_list[1]


def get_rollover_candidate(
    contracts: Sequence[ContractMaster],
    evaluation_timestamp: datetime,
) -> tuple[ContractMaster, ContractMaster] | None:
    """
    Identify rollover candidate pair (current_expiring_contract, next_active_contract).
    Informational/structural only: zero order placement.
    Returns (current, next) if current is EXPIRING and next is ACTIVE, otherwise None.
    """
    active_list = get_active_contracts(contracts, evaluation_timestamp, include_expiring=True)
    if len(active_list) < 2:
        return None

    current_c = active_list[0]
    next_c = active_list[1]

    # Rollover condition: front contract has entered EXPIRING state, and next contract is ACTIVE
    current_status = evaluate_contract_lifecycle(current_c, evaluation_timestamp)
    next_status = evaluate_contract_lifecycle(next_c, evaluation_timestamp)

    if current_status == ContractStatus.EXPIRING and next_status == ContractStatus.ACTIVE:
        return (current_c, next_c)

    return None
