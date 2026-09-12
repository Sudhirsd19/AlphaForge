"""
AlphaForge Futures and Contract Lifecycle Engine.
Phase 3 Pure Contract Governance Layer.
"""

from alphaforge.contract.enums import ContractStatus, SettlementType
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
from alphaforge.contract.models import ContractMaster
from alphaforge.contract.repository import (
    ContractMetadataProvider,
    InMemoryContractMasterRepository,
)
from alphaforge.contract.validation import (
    is_tick_aligned,
    validate_contract_master,
    validate_lot_quantity,
    validate_price_tick,
)

__all__ = [
    "ContractMaster",
    "ContractMetadataProvider",
    "ContractStatus",
    "InMemoryContractMasterRepository",
    "SettlementType",
    "evaluate_contract_lifecycle",
    "get_active_contracts",
    "get_current_active_contract",
    "get_next_contract",
    "get_rollover_candidate",
    "is_active",
    "is_expired",
    "is_expiring",
    "is_listed",
    "is_tick_aligned",
    "is_tradeable",
    "validate_contract_master",
    "validate_lot_quantity",
    "validate_price_tick",
]
