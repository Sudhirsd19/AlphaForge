"""
AlphaForge Backtest Domain Models.
Defines immutable Pydantic models for configuration, trade records, equity snapshots,
performance metrics, quant gates, and comprehensive backtest results.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.backtest.datasets import DatasetMetadata
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.cost.models import CostConfig
from alphaforge.risk.enums import TradeSide


class TraceEventType(StrEnum):
    """Authoritative discrete simulation event types for execution tracing."""

    BAR_PROCESSED = "BAR_PROCESSED"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    ORDER_SIMULATED = "ORDER_SIMULATED"
    FILL_SIMULATED = "FILL_SIMULATED"
    TRADE_CLOSED = "TRADE_CLOSED"
    EQUITY_SNAPSHOT = "EQUITY_SNAPSHOT"


class TraceEntry(BaseModel):
    """
    Immutable historical execution trace entry capturing real discrete simulation events.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    timestamp: datetime = Field(description="Timezone-aware UTC timestamp of event")
    event_type: TraceEventType = Field(description="Discrete simulation event type")
    entity_id: str = Field(description="Causal domain entity identifier")
    symbol: str | None = Field(default=None, description="Market symbol if applicable")
    side: TradeSide | None = Field(default=None, description="TradeSide if applicable")
    quantity: int | None = Field(default=None, description="Order/fill quantity if applicable")
    price: Decimal | None = Field(
        default=None, description="Reference or trigger price if applicable"
    )
    effective_price: Decimal | None = Field(
        default=None, description="Effective execution fill price"
    )
    reason: str | None = Field(default=None, description="Decision or execution reason")
    state: str | None = Field(default=None, description="Order, risk, or validation state")
    metadata: Mapping[str, Any] = Field(
        default_factory=dict, description="Deterministic event telemetry"
    )

    @field_validator("timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC timezone-aware: {v}")
        return v


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
    verify_look_ahead: bool = Field(
        default=True, description="Execute causal future mutation verification in Gate A"
    )
    verify_reproducibility: bool = Field(
        default=True, description="Execute deterministic dual-run verification in Gate G"
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
    result_canonical_hash: str | None = Field(
        default=None, description="Deterministic SHA-256 fingerprint of complete result"
    )
    trace_canonical_hash: str | None = Field(
        default=None, description="Deterministic SHA-256 fingerprint of execution trace"
    )
    execution_trace: tuple[TraceEntry, ...] = Field(
        default_factory=tuple, description="Deterministic execution event trace"
    )

    @property
    def trace(self) -> tuple[TraceEntry, ...]:
        return self.execution_trace


def _canonicalize_value(val: Any) -> Any:
    """Helper to convert nested values into deterministic JSON-serializable primitives."""
    if isinstance(val, datetime):
        return val.astimezone(UTC).isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, StrEnum):
        return val.value
    if isinstance(val, Mapping):
        return {str(k): _canonicalize_value(v) for k, v in sorted(val.items())}
    if isinstance(val, (list, tuple)):
        return [_canonicalize_value(x) for x in val]
    return val


def canonicalize_execution_trace(trace: Sequence[TraceEntry]) -> str:
    """
    Produce stable, deterministic JSON string representation of an execution trace.
    Guarantees stable event ordering, deterministic field ordering, UTC ISO timestamps,
    and stringified Decimals.
    """
    items: list[dict[str, Any]] = []
    for e in trace:
        entry_dict = {
            "timestamp": e.timestamp.astimezone(UTC).isoformat(),
            "event_type": e.event_type.value,
            "entity_id": e.entity_id,
            "symbol": e.symbol,
            "side": e.side.value if e.side is not None else None,
            "quantity": e.quantity,
            "price": str(e.price) if e.price is not None else None,
            "effective_price": str(e.effective_price) if e.effective_price is not None else None,
            "reason": e.reason,
            "state": e.state,
            "metadata": {str(k): _canonicalize_value(v) for k, v in sorted(e.metadata.items())},
        }
        items.append(entry_dict)
    return json.dumps(items, sort_keys=True, separators=(",", ":"))


def compute_trace_canonical_hash(trace: Sequence[TraceEntry]) -> str:
    """Compute deterministic SHA-256 fingerprint of canonical execution trace."""
    canonical = canonicalize_execution_trace(trace)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_backtest_result(result: BacktestResult) -> str:
    """
    Produce stable, deterministic JSON string representation of the complete BacktestResult,
    strictly excluding self-referential hash fields (result_canonical_hash, trace_canonical_hash)
    to prevent circular hashing.
    """
    payload: dict[str, Any] = {
        "backtest_run_id": result.backtest_run_id,
        "config": {
            "strategy_id": result.config.strategy_id,
            "strategy_version": result.config.strategy_version,
            "dataset_id": result.config.dataset_id,
            "start_time": result.config.start_time.astimezone(UTC).isoformat(),
            "end_time": result.config.end_time.astimezone(UTC).isoformat(),
            "initial_capital": str(result.config.initial_capital),
            "base_currency": result.config.base_currency,
            "cost_config": {
                k: _canonicalize_value(v)
                for k, v in sorted(result.config.cost_config.model_dump(mode="json").items())
            },
            "final_position_policy": result.config.final_position_policy.value,
            "conservative_same_bar_sl_first": result.config.conservative_same_bar_sl_first,
            "warmup_bars": result.config.warmup_bars,
            "seed": result.config.seed,
            "engine_version": result.config.engine_version,
            "accounting_schema_version": result.config.accounting_schema_version,
            "fill_policy_version": result.config.fill_policy_version,
            "verify_look_ahead": result.config.verify_look_ahead,
            "verify_reproducibility": result.config.verify_reproducibility,
        },
        "dataset_metadata": {
            "dataset_id": result.dataset_metadata.dataset_id,
            "checksum": result.dataset_metadata.checksum,
            "record_count": result.dataset_metadata.record_count,
            "start_timestamp": result.dataset_metadata.start_timestamp.astimezone(UTC).isoformat(),
            "end_timestamp": result.dataset_metadata.end_timestamp.astimezone(UTC).isoformat(),
            "timeframe": result.dataset_metadata.timeframe,
            "symbol": result.dataset_metadata.symbol,
            "data_source": result.dataset_metadata.data_source,
        },
        "trades": [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "side": t.side.value,
                "entry_timestamp": t.entry_timestamp.astimezone(UTC).isoformat(),
                "entry_price": str(t.entry_price),
                "entry_quantity": t.entry_quantity,
                "exit_timestamp": t.exit_timestamp.astimezone(UTC).isoformat(),
                "exit_price": str(t.exit_price),
                "exit_quantity": t.exit_quantity,
                "gross_pnl": str(t.gross_pnl),
                "fees": str(t.fees),
                "slippage": str(t.slippage),
                "other_costs": str(t.other_costs),
                "net_pnl": str(t.net_pnl),
                "return_pct": str(t.return_pct),
                "holding_duration_seconds": t.holding_duration_seconds,
                "max_favorable_excursion": str(t.max_favorable_excursion),
                "max_adverse_excursion": str(t.max_adverse_excursion),
                "entry_signal_id": t.entry_signal_id,
                "strategy_version": t.strategy_version,
                "exit_reason": t.exit_reason,
                "is_forced_close": t.is_forced_close,
            }
            for t in result.trades
        ],
        "equity_curve": [
            {
                "timestamp": s.timestamp.astimezone(UTC).isoformat(),
                "equity": str(s.equity),
                "cash": str(s.cash),
                "margin_used": str(s.margin_used),
                "realized_pnl": str(s.realized_pnl),
                "unrealized_pnl": str(s.unrealized_pnl),
                "cumulative_fees": str(s.cumulative_fees),
                "cumulative_slippage": str(s.cumulative_slippage),
                "notional_exposure": str(s.notional_exposure),
                "drawdown": str(s.drawdown),
                "drawdown_pct": str(s.drawdown_pct),
            }
            for s in result.equity_curve
        ],
        "metrics": {
            k: _canonicalize_value(v)
            for k, v in sorted(result.metrics.model_dump(mode="json").items())
        },
        "validation_status": result.validation_status.value,
        "quant_gates": [
            {
                "gate_id": g.gate_id,
                "gate_name": g.gate_name,
                "status": g.status.value,
                "reason": g.reason,
                "evidence": {str(k): _canonicalize_value(v) for k, v in sorted(g.evidence.items())},
                "warning_count": g.warning_count,
                "error_count": g.error_count,
            }
            for g in result.quant_gates
        ],
        "diagnostics": {
            str(k): _canonicalize_value(v) for k, v in sorted(result.diagnostics.items())
        },
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "completion_status": result.completion_status,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_result_canonical_hash(result: BacktestResult) -> str:
    """
    Compute deterministic SHA-256 fingerprint of complete canonical backtest result
    strictly excluding self-referential hash fields.
    """
    canonical = canonicalize_backtest_result(result)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
