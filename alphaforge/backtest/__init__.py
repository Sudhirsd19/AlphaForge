"""
AlphaForge Quantitative Backtest & Validation Engine.
Phase 10 offline historical simulation and quant gates.
"""

from alphaforge.backtest.datasets import (
    SENTINEL_EMPTY_DATETIME,
    BacktestDataset,
    DatasetMetadata,
    compute_dataset_checksum,
)
from alphaforge.backtest.engine import (
    BacktestEngine,
    compute_backtest_run_id,
)
from alphaforge.backtest.fills import (
    SimulatedFill,
    SimulatedFillEngine,
)
from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquitySnapshot,
    FinalPositionPolicy,
    QuantGateResult,
    QuantGateStatus,
    RegimeType,
    ValidationStatus,
)
from alphaforge.backtest.portfolio import PortfolioTracker
from alphaforge.backtest.reports import (
    generate_backtest_markdown_report,
    generate_backtest_text_report,
)
from alphaforge.backtest.validation import (
    DatasetPartitioner,
    MarketRegimeAnalyzer,
    OverfittingDiagnostics,
    QuantGateEvaluator,
    WalkForwardEngine,
    WalkForwardFold,
)

__all__ = [
    "BacktestConfig",
    "BacktestDataset",
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "DatasetMetadata",
    "DatasetPartitioner",
    "EquitySnapshot",
    "FinalPositionPolicy",
    "MarketRegimeAnalyzer",
    "OverfittingDiagnostics",
    "PortfolioTracker",
    "QuantGateEvaluator",
    "QuantGateResult",
    "QuantGateStatus",
    "RegimeType",
    "SENTINEL_EMPTY_DATETIME",
    "SimulatedFill",
    "SimulatedFillEngine",
    "ValidationStatus",
    "WalkForwardEngine",
    "WalkForwardFold",
    "calculate_backtest_metrics",
    "compute_backtest_run_id",
    "compute_dataset_checksum",
    "generate_backtest_markdown_report",
    "generate_backtest_text_report",
]
