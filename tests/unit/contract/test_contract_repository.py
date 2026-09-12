"""
Unit tests for Contract Master in-memory repository.
Verifies registration, idempotent duplicate handling, conflicting duplicate rejection,
and deterministic querying.
"""

from datetime import UTC, datetime, timedelta

import pytest

from alphaforge.contract.repository import InMemoryContractMasterRepository
from alphaforge.core.exceptions import ContractValidationError
from tests.unit.contract.test_contract_models import make_valid_contract


def test_repository_basic_operations() -> None:
    """Verify add, retrieve by id, count, and clear operations."""
    repo = InMemoryContractMasterRepository()
    assert repo.count() == 0

    c = make_valid_contract(contract_id="NIFTY26JUNFUT")
    repo.add_contract(c)

    assert repo.count() == 1
    retrieved = repo.get_contract("NIFTY26JUNFUT")
    assert retrieved == c

    # Case-insensitivity and whitespace stripping
    assert repo.get_contract("nifty26junfut") == c
    assert repo.get_contract("  NIFTY26JUNFUT  ") == c

    # Unknown contract returns None
    assert repo.get_contract("UNKNOWN_FUT") is None

    repo.clear()
    assert repo.count() == 0
    assert repo.get_contract("NIFTY26JUNFUT") is None


def test_repository_idempotent_duplicate_registration() -> None:
    """Verify adding identical contract master record is safely idempotent."""
    repo = InMemoryContractMasterRepository()
    c = make_valid_contract(contract_id="NIFTY26JUNFUT")

    repo.add_contract(c)
    # Re-adding identical instance should succeed silently
    repo.add_contract(c)
    assert repo.count() == 1


def test_repository_conflicting_duplicate_registration_rejected() -> None:
    """Test V: Registering conflicting metadata with same contract_id raises error."""
    repo = InMemoryContractMasterRepository()
    c1 = make_valid_contract(contract_id="NIFTY26JUNFUT", lot_size=25)
    c2 = make_valid_contract(contract_id="NIFTY26JUNFUT", lot_size=50)

    repo.add_contract(c1)
    with pytest.raises(
        ContractValidationError, match="Conflicting duplicate contract registration"
    ):
        repo.add_contract(c2)


def test_repository_get_contracts_for_underlying_sorted() -> None:
    """Test W: Retrieval by underlying symbol returns contracts in deterministic expiry order."""
    repo = InMemoryContractMasterRepository()
    now = datetime(2026, 6, 1, 9, 15, 0, tzinfo=UTC)

    c_far = make_valid_contract(
        contract_id="NIFTY26AUGFUT",
        underlying_symbol="NIFTY",
        expiry_datetime=now + timedelta(days=90),
        trading_end_datetime=now + timedelta(days=90),
    )
    c_near = make_valid_contract(
        contract_id="NIFTY26JUNFUT",
        underlying_symbol="NIFTY",
        expiry_datetime=now + timedelta(days=30),
        trading_end_datetime=now + timedelta(days=30),
    )
    c_mid = make_valid_contract(
        contract_id="NIFTY26JULFUT",
        underlying_symbol="NIFTY",
        expiry_datetime=now + timedelta(days=60),
        trading_end_datetime=now + timedelta(days=60),
    )
    c_bnf = make_valid_contract(
        contract_id="BANKNIFTY26JUNFUT",
        underlying_symbol="BANKNIFTY",
        expiry_datetime=now + timedelta(days=30),
        trading_end_datetime=now + timedelta(days=30),
    )

    repo.add_contracts([c_far, c_near, c_mid, c_bnf])
    assert repo.count() == 4

    nifty_contracts = repo.get_contracts_for_underlying("NIFTY")
    assert len(nifty_contracts) == 3
    assert [c.contract_id for c in nifty_contracts] == [
        "NIFTY26JUNFUT",
        "NIFTY26JULFUT",
        "NIFTY26AUGFUT",
    ]

    # Case-insensitive query
    assert len(repo.get_contracts_for_underlying("nifty")) == 3

    # Unknown underlying returns empty list
    assert repo.get_contracts_for_underlying("UNKNOWN") == []

    # get_all_contracts is sorted by contract_id
    all_c = repo.get_all_contracts()
    assert len(all_c) == 4
    assert [c.contract_id for c in all_c] == [
        "BANKNIFTY26JUNFUT",
        "NIFTY26AUGFUT",
        "NIFTY26JULFUT",
        "NIFTY26JUNFUT",
    ]
