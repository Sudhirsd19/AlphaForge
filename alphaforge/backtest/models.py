"""
AlphaForge Backtest Domain Models.
Defines immutable Pydantic models for configuration, trade records, equity snapshots,
performance metrics, quant gates, and comprehensive backtest results.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.backtest.datasets import DatasetMetadata
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.cost.models import CostConfig
from alphaforge.risk.enums import TradeSide


class FinalPositionPolicy(StrEnum):
    """Policy for handling positions remaining open at dataset conclusion."""

    MARK_TO_MARKET = "MARK_TO_MARKET"
    FORCE_CLOSE = "FORCE_CLOSE"


class ValidationStatus(StrEnum):
    """Overall quantitative validation classification status."""

    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RegimeType(StrEnum):
    """Market regime classification."""

    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"


class QuantGateStatus(StrEnum):
    """Individual quant gate verdict."""

    PASS = "PASS"  # noqa: S105
    WARNING = "WARNING"
    FAIL = "FAIL"


class QuantGateResult(BaseModel):
    """Immutable evaluation result for an individual quant gate."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    gate_id: str = Field(description="Gate identifier (e.g. 'Gate A')")
    gate_name: str = Field(description="Descriptive gate name")
    status: QuantGateStatus = Field(description="PASS, WARNING, or FAIL")
    reason: str = Field(description="Forensic explanation or evaluation rationale")
    evidence: Mapping[str, Any] = Field(
        default_factory=dict, description="Structured evaluation telemetry"
    )
    warning_count: int = Field(default=0, ge=0, description="Count of non-fatal warnings")
    error_count: int = Field(default=0, ge=0, description="Count of fatal gate failures")


class BacktestConfig(BaseModel):
    """
    Immutable configuration defining all parameters and assumptions for a backtest run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = Field(
        description="Identifier of evaluated strategy (e.g. AF_ORB_MOMENTUM_V1)"
    )
    strategy_version: str = Field(description="Version string of strategy rules")
    dataset_id: str = Field(description="Identifier of target market dataset")
    start_time: datetime = Field(description="Backtest simulation window start in UTC")
    end_time: datetime = Field(description="Backtest simulation window end in UTC")
    initial_capital: Decimal = Field(
        gt=Decimal("0"), description="Starting portfolio cash/collateral"
    )
    base_currency: str = Field(default="INR", description="Portfolio base currency")
    cost_config: CostConfig = Field(
        default_factory=CostConfig, description="Phase 6 transaction cost and slippage parameters"
    )
    final_position_policy: FinalPositionPolicy = Field(
        default=FinalPositionPolicy.MARK_TO_MARKET,
        description="Policy for open position at end of test",
    )
    conservative_same_bar_sl_first: bool = Field(
        default=True,
        description="Conservative OHLC ambiguity policy: trigger SL first if SL and TP both touch",
    )
    warmup_bars: int = Field(
        default=20, ge=0, description="Number of initial bars reserved for indicator warmup"
    )
    seed: int = Field(
        default=42, description="Deterministic seed for reproducible auxiliary analysis"
    )
    engine_version: str = Field(
        default="PHASE10_BACKTEST_V1", description="Backtest execution engine version"
    )
    accounting_schema_version: int = Field(
        default=1, ge=1, description="Derivatives accounting schema version"
    )
    fill_policy_version: str = Field(
        default="CONSERVATIVE_V1", description="Execution fill simulation policy version"
    )

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC timezone-aware: {v}")
        return v

    @field_validator("strategy_id", "strategy_version", "dataset_id", "base_currency")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise BacktestValidationError("Identifier fields must be non-empty strings")
        return s

    @model_validator(mode="after")
    def validate_bounds(self) -> "BacktestConfig":
        if self.start_time >= self.end_time:
            raise BacktestValidationError(
                f"start_time ({self.start_time}) must be strictly earlier "
                f"than end_time ({self.end_time})"
            )
        if not self.initial_capital.is_finite() or self.initial_capital <= Decimal("0"):
            raise BacktestValidationError(
                f"initial_capital must be positive and finite: {self.initial_capital}"
            )
        return self


class BacktestTrade(BaseModel):
    """
    Immutable historical trade record capturing full lifecycle, financial PnL,
    fees, slippage, and excursion metrics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    trade_id: str = Field(description="Unique deterministic trade identifier")
    symbol: str = Field(description="Market symbol")
    side: TradeSide = Field(description="LONG or SHORT")
    entry_timestamp: datetime = Field(description="Fill timestamp of trade entry in UTC")
    entry_price: Decimal = Field(gt=Decimal("0"), description="Effective entry execution price")
    entry_quantity: int = Field(gt=0, description="Quantity entered")
    exit_timestamp: datetime = Field(description="Fill timestamp of trade exit in UTC")
    exit_price: Decimal = Field(gt=Decimal("0"), description="Effective exit execution price")
    exit_quantity: int = Field(gt=0, description="Quantity exited")
    gross_pnl: Decimal = Field(description="Gross trading profit/loss before friction")
    fees: Decimal = Field(ge=Decimal("0"), description="Total trading transaction fees")
    slippage: Decimal = Field(ge=Decimal("0"), description="Total execution slippage cost")
    other_costs: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Other documented friction"
    )
    net_pnl: Decimal = Field(description="Net realized profit/loss after all friction")
    return_pct: Decimal = Field(description="Net return percentage on trade notional")
    holding_duration_seconds: int = Field(ge=0, description="Trade duration in seconds")
    max_favorable_excursion: Decimal = Field(
        ge=Decimal("0"), description="Maximum favorable price excursion in points"
    )
    max_adverse_excursion: Decimal = Field(
        ge=Decimal("0"), description="Maximum adverse price excursion in points"
    )
    entry_signal_id: str = Field(description="Causal StrategySignal identifier")
    strategy_version: str = Field(description="Strategy revision active at trade creation")
    exit_reason: str = Field(description="Reason for exit: STOP_LOSS, TARGET, SIGNAL, FORCED_CLOSE")
    is_forced_close: bool = Field(
        default=False, description="True if closed via end-of-test forced policy"
    )

    @field_validator("entry_timestamp", "exit_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC timezone-aware: {v}")
        return v

    @model_validator(mode="after")
    def validate_trade_invariants(self) -> "BacktestTrade":
        if self.exit_timestamp < self.entry_timestamp:
            raise BacktestValidationError(
                f"exit_timestamp ({self.exit_timestamp}) cannot precede "
                f"entry_timestamp ({self.entry_timestamp})"
            )
        if self.exit_quantity > self.entry_quantity:
            raise BacktestValidationError(
                f"exit_quantity ({self.exit_quantity}) cannot exceed "
                f"entry_quantity ({self.entry_quantity})"
            )
        return self


class EquitySnapshot(BaseModel):
    """
    Chronological snapshot of portfolio collateral, margin, PnL, and drawdown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    timestamp: datetime = Field(description="UTC timestamp of the snapshot")
    equity: Decimal = Field(description="Total portfolio equity")
    cash: Decimal = Field(description="Available cash/collateral")
    margin_used: Decimal = Field(default=Decimal("0"), description="Current margin requirement")
    realized_pnl: Decimal = Field(description="Cumulative realized trading profit/loss")
    unrealized_pnl: Decimal = Field(description="Mark-to-market unrealized profit/loss")
    cumulative_fees: Decimal = Field(description="Cumulative transaction fees incurred")
    cumulative_slippage: Decimal = Field(description="Cumulative adverse slippage incurred")
    notional_exposure: Decimal = Field(description="Current open notional market exposure")
    drawdown: Decimal = Field(
        ge=Decimal("0"), description="Drawdown from equity peak in monetary units"
    )
    drawdown_pct: Decimal = Field(ge=Decimal("0"), description="Drawdown fraction relative to peak")

    @field_validator("timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC timezone-aware: {v}")
        return v


class BacktestMetrics(BaseModel):
    """
    Comprehensive quantitative performance, risk-adjusted, and trade metrics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    # Returns
    total_return: Decimal = Field(description="Total net return in monetary units")
    total_return_pct: Decimal = Field(
        description="Total net return as percentage of initial capital"
    )
    annualized_return_pct: Decimal | None = Field(
        default=None, description="Annualized net return percentage (None if sample insufficient)"
    )

    # Trade Statistics
    total_trades: int = Field(ge=0, description="Total completed round-trip trades")
    winning_trades: int = Field(ge=0, description="Count of profitable trades")
    losing_trades: int = Field(ge=0, description="Count of unprofitable trades")
    break_even_trades: int = Field(ge=0, description="Count of exactly zero PnL trades")
    win_rate: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"), description="Fraction of winning trades"
    )
    loss_rate: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"), description="Fraction of losing trades"
    )
    average_win: Decimal = Field(ge=Decimal("0"), description="Average profit of winning trades")
    average_loss: Decimal = Field(
        ge=Decimal("0"), description="Average loss of losing trades (positive)"
    )
    payoff_ratio: Decimal | None = Field(
        default=None, description="Ratio of average win to average loss (None if zero loss)"
    )
    expectancy: Decimal = Field(description="Expected monetary return per trade")
    profit_factor: Decimal | None = Field(
        default=None, description="Gross profit divided by gross loss (None if zero loss)"
    )

    # Risk Metrics
    max_drawdown: Decimal = Field(
        ge=Decimal("0"), description="Peak-to-trough maximum drawdown amount"
    )
    max_drawdown_pct: Decimal = Field(
        ge=Decimal("0"), description="Maximum drawdown percentage relative to peak"
    )
    max_drawdown_duration_seconds: int = Field(
        ge=0, description="Duration in seconds of longest drawdown recovery period"
    )
    volatility_annualized: Decimal | None = Field(
        default=None, description="Annualized volatility of returns (None if sample insufficient)"
    )
    downside_volatility_annualized: Decimal | None = Field(
        default=None, description="Annualized downside volatility (None if sample insufficient)"
    )
    sharpe_ratio: Decimal | None = Field(
        default=None,
        description="Annualized Sharpe ratio (None if sample insufficient or zero vol)",
    )
    sortino_ratio: Decimal | None = Field(
        default=None,
        description="Annualized Sortino ratio (None if sample insufficient or zero vol)",
    )
    calmar_ratio: Decimal | None = Field(
        default=None, description="Calmar ratio: Annualized return / max drawdown pct"
    )

    # Exposure & Financial Totals
    average_exposure: Decimal = Field(
        description="Average notional exposure across all evaluation bars"
    )
    max_exposure: Decimal = Field(description="Peak notional exposure across all evaluation bars")
    gross_pnl: Decimal = Field(description="Cumulative gross PnL across all trades")
    total_fees: Decimal = Field(ge=Decimal("0"), description="Total transaction fees incurred")
    total_slippage: Decimal = Field(
        ge=Decimal("0"), description="Total adverse slippage loss incurred"
    )
    net_pnl: Decimal = Field(description="Net trading PnL: gross_pnl - total_fees - total_slippage")


class BacktestResult(BaseModel):
    """
    Immutable top-level container capturing complete backtest output, diagnostics,
    quant gate audits, and execution lineage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    backtest_run_id: str = Field(description="Deterministic execution run identifier (BT-RUN-...)")
    config: BacktestConfig = Field(description="Configuration used for simulation")
    dataset_metadata: DatasetMetadata = Field(description="Market dataset provenance metadata")
    trades: tuple[BacktestTrade, ...] = Field(description="Chronological list of completed trades")
    equity_curve: tuple[EquitySnapshot, ...] = Field(description="Chronological equity snapshots")
    metrics: BacktestMetrics = Field(description="Comprehensive calculated performance metrics")
    validation_status: ValidationStatus = Field(description="Overall validation verdict")
    quant_gates: tuple[QuantGateResult, ...] = Field(description="Detailed Quant Gates A-J audits")
    diagnostics: Mapping[str, Any] = Field(
        default_factory=dict, description="Diagnostic telemetry and overfitting metrics"
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple, description="Non-fatal warnings recorded"
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple, description="Validation failures recorded"
    )
    completion_status: str = Field(
        default="COMPLETED", description="Run status: COMPLETED or FAILED"
    )
