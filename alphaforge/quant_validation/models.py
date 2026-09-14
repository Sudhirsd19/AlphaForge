"""
Data models and enumerations for Phase 18 Quantitative Validation Engine.
Provides strict immutable Pydantic schemas for:
- Validation splits (IS/OOS)
- Walk-forward folds and summary reports
- Combinatorial Purged & Embargoed Cross-Validation (CPCV)
- Regime-conditioned performance
- Parameter sensitivity & cliff-edges
- Cost stress testing (brokerage, fees, slippage)
- Monte Carlo / bootstrap distributions
- Institutional robustness evaluation gates
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.core.exceptions import DataIntegrityError


class RegimeType(StrEnum):
    """Market regime classification."""

    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    HIGH_VOLUME = "HIGH_VOLUME"
    LOW_VOLUME = "LOW_VOLUME"


class RobustnessGateResult(StrEnum):
    """Institutional robustness certification verdict."""

    ROBUST = "ROBUST"
    CONDITIONALLY_ROBUST = "CONDITIONALLY_ROBUST"
    UNSTABLE = "UNSTABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"


class ValidationSplit(BaseModel):
    """Temporal split between In-Sample (IS) and Out-of-Sample (OOS) data."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime
    embargo_duration_seconds: int = Field(ge=0)
    train_bars: int = Field(ge=1)
    oos_bars: int = Field(ge=1)
    train_ratio: float = Field(gt=0.0, lt=1.0)

    @field_validator("oos_start")
    @classmethod
    def validate_temporal_order(cls, v: datetime, info: Any) -> datetime:
        train_end = info.data.get("train_end")
        if train_end and v < train_end:
            raise DataIntegrityError(
                f"OOS start ({v}) cannot be before training end ({train_end}) "
                "- future data leakage!"
            )
        return v


class WalkForwardFold(BaseModel):
    """Single fold within a rolling Walk-Forward analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    fold_index: int = Field(ge=0)
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    test_start: datetime
    test_end: datetime
    is_trades: int = Field(ge=0)
    oos_trades: int = Field(ge=0)
    is_sharpe: float | None = None
    oos_sharpe: float | None = None
    is_profit_factor: float | None = None
    oos_profit_factor: float | None = None
    is_net_pnl: Decimal = Decimal("0")
    oos_net_pnl: Decimal = Decimal("0")
    efficiency_ratio: float | None = None  # OOS Sharpe / IS Sharpe


class WalkForwardReport(BaseModel):
    """Aggregated walk-forward evaluation across all folds."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    total_folds: int = Field(ge=1)
    folds: list[WalkForwardFold]
    mean_is_sharpe: float | None = None
    mean_oos_sharpe: float | None = None
    walk_forward_efficiency: float | None = None  # Aggregated OOS/IS ratio
    positive_oos_ratio: float = Field(ge=0.0, le=1.0)
    is_stable: bool


class CPCVPartition(BaseModel):
    """Combinatorial Purged & Embargoed Cross-Validation partition."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    partition_id: int = Field(ge=0)
    total_groups: int = Field(ge=2)
    test_group_indices: list[int]
    train_bar_count: int = Field(ge=0)
    test_bar_count: int = Field(ge=0)
    purged_bar_count: int = Field(ge=0)
    embargoed_bar_count: int = Field(ge=0)


class RegimePerformance(BaseModel):
    """Strategy performance conditioned on a specific market regime."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    regime: RegimeType
    bar_count: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    win_rate: Decimal = Decimal("0")
    profit_factor: Decimal | None = None
    net_pnl: Decimal = Decimal("0")
    sharpe_ratio: float | None = None
    max_drawdown_pct: Decimal = Decimal("0")
    is_regime_failing: bool = False  # True if catastrophic loss occurs in this regime


class ParameterSensitivityResult(BaseModel):
    """Sensitivity analysis for a single strategy parameter."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    parameter_name: str
    baseline_value: float
    perturbations: list[dict[str, Any]]  # [{"delta_pct": -10, "value": 18, "metric": 1.45}]
    cliff_edge_detected: bool
    max_metric_degradation_pct: float
    sensitivity_gradient: float  # |dMetric / dParam|


class CostStressResult(BaseModel):
    """Stress test evaluating strategy resilience to fees, spread, and slippage."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    baseline_fees: Decimal
    baseline_slippage: Decimal
    baseline_net_pnl: Decimal
    stress_1_5x_pnl: Decimal
    stress_2_0x_pnl: Decimal
    stress_3_0x_pnl: Decimal
    break_even_slippage_bps: float | None = None
    robust_to_2x_costs: bool
    robust_to_3x_costs: bool


class MonteCarloDistribution(BaseModel):
    """Monte Carlo bootstrap results over trade returns and sequences."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    iterations: int = Field(ge=100)
    trade_count: int = Field(ge=0)
    mean_drawdown_pct: float
    p95_drawdown_pct: float  # 95th percentile worst drawdown
    p99_drawdown_pct: float  # 99th percentile worst drawdown
    max_simulated_drawdown_pct: float
    probability_of_ruin_pct: float  # Probability of DD > 20%
    sharpe_ci_lower: float | None = None  # 95% Confidence Interval
    sharpe_ci_upper: float | None = None
    p_value_zero_expectancy: float  # Null hypothesis test: expectancy <= 0


class InstitutionalRobustnessReport(BaseModel):
    """
    Authoritative Quantitative Validation Report for institutional certification.
    Grounds all verdicts in empirical testing without mock numbers or profitability promises.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    report_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    overall_gate: RobustnessGateResult
    gate_reasons: list[str]
    sample_size: int = Field(ge=0)
    has_sufficient_sample: bool
    sample_size_caveat: str | None = None

    # Performance & Risk
    expectancy: Decimal
    profit_factor: Decimal | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown_pct: Decimal

    # Subsystem validation modules
    walk_forward: WalkForwardReport | None = None
    regime_analysis: list[RegimePerformance] = Field(default_factory=list)
    sensitivity_analysis: list[ParameterSensitivityResult] = Field(default_factory=list)
    cost_stress: CostStressResult | None = None
    monte_carlo: MonteCarloDistribution | None = None

    # Disclaimers
    regulatory_disclaimer: str = (
        "Institutional quantitative validation output for risk assessment and "
        "engineering verification only. Does NOT claim or guarantee future profits, "
        "win rates, or risk elimination."
    )
