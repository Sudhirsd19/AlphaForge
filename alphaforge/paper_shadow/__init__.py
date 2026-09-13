"""
AlphaForge Phase 16: Paper / Shadow Trading Package.

Provides a fail-closed, deterministic Paper and Shadow trading validation layer surrounding
frozen Phase 0–15 components without modifying frozen trading mathematics, risk formulas,
order FSM, or audit ledger logic.
"""

from __future__ import annotations

from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import (
    FillType,
    ForwardRunState,
    MarketDataAnomalyType,
    OHLCResolutionPolicy,
    OrderRoutingMode,
    PaperShadowMode,
    ShadowComparisonOutcome,
)
from alphaforge.paper_shadow.fill_simulator import (
    BracketEvaluationResult,
    DeterministicFillSimulator,
)
from alphaforge.paper_shadow.forward_runner import ForwardValidationRunner
from alphaforge.paper_shadow.market_data_validator import (
    MarketDataValidationResult,
    PaperMarketDataValidator,
)
from alphaforge.paper_shadow.models import (
    ForwardRunReport,
    MarketEvent,
    PaperPerformanceMetrics,
    PaperShadowConfig,
    PaperTradeRecord,
    ShadowObservationRecord,
)
from alphaforge.paper_shadow.order_router import PaperShadowOrderRouter
from alphaforge.paper_shadow.pnl_tracker import (
    ActivePositionState,
    PaperPnLTracker,
)
from alphaforge.paper_shadow.reconciler_adapter import PaperShadowReconciler
from alphaforge.paper_shadow.shadow_comparator import ShadowComparator

__all__ = [
    # Enums
    "PaperShadowMode",
    "OHLCResolutionPolicy",
    "FillType",
    "MarketDataAnomalyType",
    "OrderRoutingMode",
    "ShadowComparisonOutcome",
    "ForwardRunState",
    # Models
    "PaperShadowConfig",
    "MarketEvent",
    "PaperTradeRecord",
    "ShadowObservationRecord",
    "PaperPerformanceMetrics",
    "ForwardRunReport",
    # Validators & Simulators
    "PaperMarketDataValidator",
    "MarketDataValidationResult",
    "DeterministicFillSimulator",
    "BracketEvaluationResult",
    # Accounting & Routing
    "PaperPnLTracker",
    "ActivePositionState",
    "PaperShadowOrderRouter",
    "ShadowComparator",
    "PaperShadowReconciler",
    # Engine & Runner
    "PaperShadowEngine",
    "ForwardValidationRunner",
]
