"""
AlphaForge Contract Metadata Provider Boundary and In-Memory Repository.
Defines clean abstract interfaces for contract metadata sources and provides a
deterministic in-memory store.
Zero external network/broker connections.
"""

from collections.abc import Sequence
from typing import Protocol

from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import ContractValidationError


class ContractMetadataProvider(Protocol):
    """
    Abstract interface boundary for contract metadata sources.
    Decouples lifecycle engine from external venue feeds or databases.
    """

    def get_contract(self, contract_id: str) -> ContractMaster | None:
        """Retrieve contract master metadata by unique contract identifier."""
        ...

    def get_contracts_for_underlying(self, underlying_symbol: str) -> list[ContractMaster]:
        """Retrieve all known contract master records for an underlying symbol."""
        ...

    def get_all_contracts(self) -> list[ContractMaster]:
        """Retrieve all known contracts across all instruments."""
        ...


class InMemoryContractMasterRepository:
    """
    Deterministic in-memory repository implementing ContractMetadataProvider.
    Enforces uniqueness and immutability of contract master records.
    """

    def __init__(self) -> None:
        # Key: contract_id -> ContractMaster
        self._by_id: dict[str, ContractMaster] = {}
        # Key: underlying_symbol -> list[ContractMaster]
        self._by_underlying: dict[str, list[ContractMaster]] = {}

    def clear(self) -> None:
        """Clear all stored contract metadata."""
        self._by_id.clear()
        self._by_underlying.clear()

    def count(self) -> int:
        """Return total count of registered contracts."""
        return len(self._by_id)

    def add_contract(self, contract: ContractMaster) -> None:
        """
        Register a validated ContractMaster record.
        Idempotent for identical records; raises ContractValidationError on conflicting duplicates.
        """
        cid = contract.contract_id
        if cid in self._by_id:
            existing = self._by_id[cid]
            if existing == contract:
                return
            raise ContractValidationError(
                f"Conflicting duplicate contract registration for contract_id '{cid}'"
            )

        self._by_id[cid] = contract

        underlying = contract.underlying_symbol
        if underlying not in self._by_underlying:
            self._by_underlying[underlying] = []
        self._by_underlying[underlying].append(contract)
        # Keep underlying list sorted deterministically by expiry_datetime
        self._by_underlying[underlying].sort(key=lambda c: (c.expiry_datetime, c.contract_id))

    def add_contracts(self, contracts: Sequence[ContractMaster]) -> None:
        """Register multiple contract master records."""
        for c in contracts:
            self.add_contract(c)

    def get_contract(self, contract_id: str) -> ContractMaster | None:
        """Retrieve contract master record by unique contract ID."""
        return self._by_id.get(contract_id.strip().upper())

    def get_contracts_for_underlying(self, underlying_symbol: str) -> list[ContractMaster]:
        """Retrieve all contracts for an underlying symbol in deterministic expiry order."""
        return list(self._by_underlying.get(underlying_symbol.strip().upper(), []))

    def get_all_contracts(self) -> list[ContractMaster]:
        """Retrieve all registered contracts sorted deterministically by contract ID."""
        return sorted(self._by_id.values(), key=lambda c: c.contract_id)
