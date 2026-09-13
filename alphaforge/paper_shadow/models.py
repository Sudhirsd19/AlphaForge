"""
AlphaForge Paper / Shadow Trading Domain Models.

Defines immutable Pydantic models for configuration, market events, trade records,
shadow observations, performance metrics, and forward validation reports.
All financial arithmetic strictly uses fixed-point Decimal precision.
All timestamps enforce timezone-aware UTC.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path  # noqa: TC003
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.cost.models import CostConfig
from alphaforge.data.models import MarketCandle  # noqa: TC001
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import DeploymentSafetyError
from alphaforge.paper_shadow.enums import (
    OHLCResolutionPolicy,
    PaperShadowMode,
    ShadowComparisonOutcome,
)
from alphaforge.risk.enums import TradeSide  # noqa: TC001


class PaperShadowConfig(BaseModel):
    """
    Immutable configuration for Paper / Shadow trading validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    mode: PaperShadowMode = Field(
        default=PaperShadowMode.PAPER,
        description="Execution mode: PAPER or SHADOW",
    )
    deployment_config: DeploymentConfig = Field(
        default_factory=DeploymentConfig,
        description="Underlying Phase 15 deployment configuration",
    )
    cost_config: CostConfig = Field(
        default_factory=CostConfig,
        description="Phase 6 transaction cost and slippage configuration",
    )
    ohlc_policy: OHLCResolutionPolicy = Field(
        default=OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE,
        description="Policy for resolving same-bar SL/TP ambiguity (defaults to SL first)",
    )
    stale_data_threshold_seconds: int = Field(
        default=15,
        ge=1,
        description="Base stale threshold in seconds (scaled for slower timeframes)",
    )
    enforce_session_boundaries: bool = Field(
        default=True,
        description="Enforce trading session boundaries and market close rules",
    )
    contract_multiplier: Decimal = Field(
        default=Decimal("1"),
        gt=Decimal("0"),
        description="Contract multiplier for notional and P&L calculation",
    )
    lot_size: int = Field(
        default=1,
        gt=0,
        description="Standard trade lot size",
    )
    initial_capital: Decimal = Field(
        default=Decimal("1000000"),
        gt=Decimal("0"),
        description="Initial paper trading starting capital",
    )
    simulated_latency_ms: int = Field(
        default=0,
        ge=0,
        description="Deterministic simulated execution latency in milliseconds",
    )
    persistence_path: Path | None = Field(
        default=None,
        description="Optional path for state checkpointing and recovery",
    )
    schema_version: str = Field(
        default="1.0.0",
        description="Configuration schema version",
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_default_deployment_config(cls, data: dict[str, Any] | Any) -> dict[str, Any] | Any:
        """Resolve appropriate default deployment environment if not explicitly provided."""
        if isinstance(data, dict) and "deployment_config" not in data:
            mode_val = data.get("mode", PaperShadowMode.PAPER)
            if mode_val in (PaperShadowMode.SHADOW, "SHADOW"):
                data["deployment_config"] = DeploymentConfig(
                    environment=DeploymentEnvironment.SHADOW
                )
            else:
                data["deployment_config"] = DeploymentConfig(
                    environment=DeploymentEnvironment.PAPER
                )
        return data

    @model_validator(mode="after")
    def validate_config_invariants(self) -> PaperShadowConfig:
        """Verify contract multiplier is finite and fail closed on environment mismatch."""
        if not self.contract_multiplier.is_finite() or self.contract_multiplier <= Decimal("0"):
            raise DataIntegrityError(
                f"contract_multiplier must be a positive finite Decimal: {self.contract_multiplier}"
            )
        # Fail closed on environment mismatch - zero silent rewrites
        if self.mode == PaperShadowMode.PAPER:
            if self.deployment_config.environment != DeploymentEnvironment.PAPER:
                raise DeploymentSafetyError(
                    f"Environment mismatch: PaperShadowMode.PAPER cannot run in "
                    f"DeploymentEnvironment.{self.deployment_config.environment.name}"
                )
        elif self.mode == PaperShadowMode.SHADOW:
            if self.deployment_config.environment != DeploymentEnvironment.SHADOW:
                raise DeploymentSafetyError(
                    f"Environment mismatch: PaperShadowMode.SHADOW cannot run in "
                    f"DeploymentEnvironment.{self.deployment_config.environment.name}"
                )
        else:
            raise DeploymentSafetyError(f"Unsupported PaperShadowMode: {self.mode}")
        return self

    def compute_config_hash(self) -> str:
        """Compute deterministic SHA-256 hash of configuration parameters."""
        raw = (
            f"{self.mode.value}|{self.ohlc_policy.value}|"
            f"{self.cost_config.entry_fee_rate}|{self.cost_config.exit_fee_rate}|"
            f"{self.cost_config.entry_slippage_rate}|{self.cost_config.exit_slippage_rate}|"
            f"{self.cost_config.fixed_cost_per_trade}|{self.contract_multiplier}|"
            f"{self.lot_size}|{self.simulated_latency_ms}|{self.schema_version}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()


class MarketEvent(BaseModel):
    """
    Immutable envelope for real-time or simulated market data delivery.
    Enforces strict chronological causality and explicit timestamp semantics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(description="Deterministic unique event identifier")
    sequence: int = Field(ge=0, description="Strict monotonically increasing sequence number")
    candle: MarketCandle = Field(description="Canonical market candle")
    market_timestamp: datetime = Field(description="Exchange timestamp in UTC")
    receipt_timestamp: datetime = Field(description="System arrival/receipt timestamp in UTC")
    symbol: str = Field(description="Trading symbol")

    @field_validator("event_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise DataIntegrityError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("market_timestamp", "receipt_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_causality(self) -> MarketEvent:
        """Receipt timestamp cannot precede exchange market timestamp."""
        if self.receipt_timestamp < self.market_timestamp:
            raise DataIntegrityError(
                f"Causality violation: receipt_timestamp {self.receipt_timestamp} "
                f"precedes market_timestamp {self.market_timestamp}"
            )
        return self


class PaperTradeRecord(BaseModel):
    """
    Immutable record of an executed paper trade with complete P&L, fee, and slippage breakdown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    trade_id: str = Field(description="Unique deterministic trade identifier")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="Trade side: LONG | SHORT")
    quantity: int = Field(gt=0, description="Executed quantity")
    entry_order_id: str = Field(description="Deterministic client entry order ID")
    exit_order_id: str | None = Field(
        default=None, description="Deterministic client exit order ID"
    )
    entry_timestamp: datetime = Field(description="Entry execution timestamp in UTC")
    exit_timestamp: datetime | None = Field(
        default=None, description="Exit execution timestamp in UTC"
    )
    entry_price: Decimal = Field(gt=Decimal("0"), description="Effective entry price")
    exit_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Effective exit price"
    )
    gross_pnl: Decimal = Field(default=Decimal("0"), description="Gross monetary P&L")
    net_pnl: Decimal = Field(default=Decimal("0"), description="Net monetary P&L after friction")
    fees: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Total transaction fees"
    )
    slippage_loss: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Total monetary slippage loss"
    )
    is_closed: bool = Field(default=False, description="True if position is completely closed")
    exit_reason: str | None = Field(default=None, description="Reason for trade closure")

    @field_validator("trade_id", "symbol", "entry_order_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise DataIntegrityError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("entry_timestamp")
    @classmethod
    def validate_entry_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @field_validator("exit_timestamp")
    @classmethod
    def validate_exit_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v)):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v


class ShadowObservationRecord(BaseModel):
    """
    Immutable diagnostic record emitted during SHADOW mode observation.
    Captures what the system would have done without executing real or paper orders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    observation_id: str = Field(description="Unique deterministic observation identifier")
    timestamp: datetime = Field(description="Observation context timestamp in UTC")
    symbol: str = Field(description="Trading symbol")
    market_event_id: str = Field(description="Associated market event ID")
    signal_decision: str = Field(description="ACCEPT | REJECT | NO_SIGNAL")
    signal_direction: str = Field(description="LONG | SHORT | FLAT")
    signal_id: str | None = Field(default=None, description="Signal ID if generated")
    risk_decision: str | None = Field(default=None, description="APPROVED | REJECTED")
    risk_reason_code: str | None = Field(default=None, description="Risk reason code if evaluated")
    intended_client_order_id: str | None = Field(default=None, description="Hypothetical order ID")
    intended_quantity: int | None = Field(default=None, ge=0, description="Hypothetical quantity")
    hypothetical_fill_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Hypothetical fill price"
    )
    hypothetical_fill_qty: int | None = Field(
        default=None, ge=0, description="Hypothetical fill quantity"
    )
    hypothetical_position_qty: int = Field(
        default=0, ge=0, description="Hypothetical position quantity"
    )
    hypothetical_realized_pnl: Decimal = Field(
        default=Decimal("0"), description="Hypothetical realized P&L"
    )
    hypothetical_unrealized_pnl: Decimal = Field(
        default=Decimal("0"), description="Hypothetical unrealized P&L"
    )
    evaluation_latency_ns: int = Field(
        default=0, ge=0, description="Diagnostic evaluation duration in nanoseconds"
    )
    market_price_reference: Decimal = Field(
        gt=Decimal("0"), description="Market reference price at evaluation"
    )
    subsequent_price_check: Decimal | None = Field(
        default=None, description="Subsequent candle price for outcome comparison"
    )
    comparison_outcome: ShadowComparisonOutcome = Field(
        default=ShadowComparisonOutcome.NO_SIGNAL,
        description="Diagnostic comparison outcome",
    )
    notes: str = Field(default="", description="Diagnostic audit notes")

    @field_validator("timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v


class PaperPerformanceMetrics(BaseModel):
    """
    Immutable quantitative summary of paper trading performance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    trade_count: int = Field(ge=0, description="Total completed round-trip trades")
    winning_trades: int = Field(ge=0, description="Count of trades with net P&L > 0")
    losing_trades: int = Field(ge=0, description="Count of trades with net P&L < 0")
    win_rate: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"), description="Winning trades fraction"
    )
    gross_pnl: Decimal = Field(description="Total gross monetary P&L")
    net_pnl: Decimal = Field(description="Total net monetary P&L after friction")
    total_fees: Decimal = Field(ge=Decimal("0"), description="Cumulative transaction fees")
    total_slippage: Decimal = Field(
        ge=Decimal("0"), description="Cumulative monetary slippage loss"
    )
    max_drawdown: Decimal = Field(
        ge=Decimal("0"), description="Maximum monetary drawdown from peak"
    )
    max_drawdown_pct: Decimal = Field(
        ge=Decimal("0"), description="Maximum percentage drawdown from peak equity"
    )
    average_trade_pnl: Decimal = Field(description="Average net P&L per trade")
    profit_factor: Decimal = Field(ge=Decimal("0"), description="Gross profits / Gross losses")
    current_exposure: Decimal = Field(
        ge=Decimal("0"), description="Current active notional exposure"
    )
    open_positions_count: int = Field(ge=0, description="Count of currently active open positions")
    realized_pnl: Decimal = Field(description="Cumulative realized P&L")
    unrealized_pnl: Decimal = Field(description="Current unrealized mark-to-market P&L")


class ForwardRunReport(BaseModel):
    """
    Immutable end-of-run forensic report for Paper / Shadow forward validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str = Field(description="Unique deterministic run identifier")
    mode: PaperShadowMode = Field(description="PAPER or SHADOW")
    start_time: datetime = Field(description="Run start timestamp in UTC")
    end_time: datetime = Field(description="Run completion timestamp in UTC")
    total_candles_processed: int = Field(ge=0, description="Total candles processed")
    total_signals_generated: int = Field(ge=0, description="Total strategy signals emitted")
    total_orders_submitted: int = Field(ge=0, description="Total orders submitted to broker/sim")
    total_fills_executed: int = Field(ge=0, description="Total fills executed")
    reconciliation_passes: int = Field(ge=0, description="Total reconciliation cycles completed")
    reconciliation_mismatches_detected: int = Field(
        default=0, ge=0, description="Count of reconciliation mismatches detected"
    )
    metrics: PaperPerformanceMetrics = Field(description="Quantitative performance metrics")
    configuration_hash: str = Field(description="SHA-256 hash of configuration")
    code_revision: str = Field(description="Git commit hash of running code")
    audit_events_count: int = Field(ge=0, description="Total audit ledger events committed")
    observability_events_count: int = Field(
        ge=0, description="Total observability events dispatched"
    )
    no_live_orders_submitted: bool = Field(
        default=True,
        description="Authoritative invariant verification that ZERO live orders were submitted",
    )

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_safety_invariant(self) -> ForwardRunReport:
        """Explicit safety gate: no_live_orders_submitted must be strictly True."""
        if not self.no_live_orders_submitted:
            raise DataIntegrityError(
                "Critical Safety Violation: Live orders submitted in Paper/Shadow run!"
            )
        return self
