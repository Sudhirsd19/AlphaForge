"""
AlphaForge Cost & Slippage Module.
Deterministic, side-aware cost and slippage evaluation engine (Phase 6).
Provides transaction cost, execution slippage, gross/net P&L, and R-multiple modeling.
"""

from alphaforge.cost.calculator import (
    calculate_effective_price,
    calculate_fee,
    calculate_gross_pnl,
    calculate_net_pnl,
    calculate_r_multiples,
    calculate_transaction_costs,
)
from alphaforge.cost.engine import CostEngine, evaluate_trade_cost
from alphaforge.cost.enums import CostDecisionState, CostReasonCode
from alphaforge.cost.models import CostConfig, CostInput, CostResult

__all__ = [
    "CostConfig",
    "CostDecisionState",
    "CostEngine",
    "CostInput",
    "CostReasonCode",
    "CostResult",
    "calculate_effective_price",
    "calculate_fee",
    "calculate_gross_pnl",
    "calculate_net_pnl",
    "calculate_r_multiples",
    "calculate_transaction_costs",
    "evaluate_trade_cost",
]
