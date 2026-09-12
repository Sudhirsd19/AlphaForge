"""
AlphaForge Cost & Slippage Domain Models.
Defines immutable data models for cost configurations, calculation inputs, and audit results.
All monetary fields strictly enforce fixed-point Decimal arithmetic without floating-point.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import CostValidationError
from alphaforge.cost.enums import CostDecisionState, CostReasonCode
from alphaforge.risk.enums import TradeSide


class CostConfig(BaseModel):
    """
    Immutable configuration for transaction costs and execution slippage.

    NOTE: Initial default rates (0.0) are explicit MODEL ASSUMPTIONS / NOT BROKER-VERIFIED.
    Any non-zero tariff assumption must be explicitly configured by callers.
    Rates are represented as decimal fractions (e.g. 0.0005 = 0.05%).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    entry_fee_rate: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Transaction fee fraction on entry notional (e.g. 0.0005 for 0.05%)",
    )
    exit_fee_rate: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Transaction fee fraction on exit notional (e.g. 0.0005 for 0.05%)",
    )
    entry_slippage_rate: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Execution price slippage penalty on entry as a fraction of reference price",
    )
    exit_slippage_rate: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Execution price slippage penalty on exit as a fraction of reference price",
    )
    fixed_cost_per_trade: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Fixed round-trip transaction friction/brokerage fee in monetary units",
    )
    calculation_version: str = Field(
        default="PHASE6_COST_V1",
        description="Deterministic calculation engine version",
    )

    @model_validator(mode="after")
    def validate_config_finiteness(self) -> "CostConfig":
        """Verify all rates and fees are finite Decimals and non-negative."""
        for field_name in (
            "entry_fee_rate",
            "exit_fee_rate",
            "entry_slippage_rate",
            "exit_slippage_rate",
            "fixed_cost_per_trade",
        ):
            val: Decimal = getattr(self, field_name)
            if not val.is_finite() or val.is_nan():
                raise CostValidationError(f"{field_name} must be a finite Decimal: {val}")
            if val < Decimal("0"):
                raise CostValidationError(f"{field_name} cannot be negative: {val}")
        return self


class CostInput(BaseModel):
    """
    Immutable trade input for expected transaction cost and slippage evaluation.
    Receives explicit reference prices and position sizing; does not connect to brokers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    side: TradeSide = Field(description="LONG | SHORT")
    entry_reference_price: Decimal = Field(gt=Decimal("0"), description="Reference entry price")
    exit_reference_price: Decimal = Field(gt=Decimal("0"), description="Reference exit price")
    quantity: int = Field(gt=0, description="Proposed trade quantity in units")
    contract_multiplier: Decimal = Field(
        gt=Decimal("0"),
        description="Contract multiplier (strictly required; no implicit 1 fallback)",
    )
    risk_amount: Decimal = Field(
        gt=Decimal("0"),
        description="Monetary risk denominator from Phase 5 risk budget",
    )
    cost_config: CostConfig | None = Field(
        default=None,
        description="Optional custom CostConfig; defaults to CostConfig() if None",
    )
    symbol: str | None = Field(default=None, description="Optional instrument symbol for audit")
    signal_id: str | None = Field(default=None, description="Optional signal identifier for audit")
    timestamp: datetime | None = Field(
        default=None, description="Optional evaluation context timestamp"
    )

    @field_validator("symbol", "signal_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise CostValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise CostValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_input_finiteness(self) -> "CostInput":
        """Ensure numeric inputs are strictly finite positive Decimals."""
        for field_name in (
            "entry_reference_price",
            "exit_reference_price",
            "contract_multiplier",
            "risk_amount",
        ):
            val: Decimal = getattr(self, field_name)
            if not val.is_finite() or val.is_nan():
                raise CostValidationError(f"{field_name} must be a finite Decimal: {val}")
            if val <= Decimal("0"):
                raise CostValidationError(f"{field_name} must be strictly positive: {val}")
        if self.quantity <= 0:
            raise CostValidationError(
                f"quantity must be a strictly positive integer: {self.quantity}"
            )
        return self


class CostResult(BaseModel):
    """
    Immutable audit record representing the definitive cost, slippage, and net P&L calculation.
    Provides complete traceability for every evaluated trade without external side effects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision: CostDecisionState = Field(description="VALID | INVALID")
    reason_code: CostReasonCode = Field(description="Deterministic machine-readable reason code")
    reason: str = Field(description="Human-readable audit explanation")
    side: TradeSide = Field(description="LONG | SHORT")
    reference_entry_price: Decimal | None = Field(
        default=None, description="Reference entry price before slippage"
    )
    effective_entry_price: Decimal | None = Field(
        default=None, description="Effective entry price after slippage"
    )
    reference_exit_price: Decimal | None = Field(
        default=None, description="Reference exit price before slippage"
    )
    effective_exit_price: Decimal | None = Field(
        default=None, description="Effective exit price after slippage"
    )
    quantity: int | None = Field(default=None, description="Trade quantity in units")
    contract_multiplier: Decimal | None = Field(default=None, description="Contract multiplier")
    entry_notional: Decimal | None = Field(
        default=None, description="Entry notional value at effective entry price"
    )
    exit_notional: Decimal | None = Field(
        default=None, description="Exit notional value at effective exit price"
    )
    entry_fee: Decimal | None = Field(default=None, description="Transaction fee on entry notional")
    exit_fee: Decimal | None = Field(default=None, description="Transaction fee on exit notional")
    fixed_cost: Decimal | None = Field(default=None, description="Fixed round-trip transaction fee")
    transaction_cost: Decimal | None = Field(
        default=None, description="Total round-trip transaction cost (entry + exit + fixed)"
    )
    gross_pnl: Decimal | None = Field(
        default=None, description="Gross monetary P&L on effective execution prices"
    )
    net_pnl: Decimal | None = Field(
        default=None, description="Net monetary P&L after deducting transaction friction"
    )
    risk_amount: Decimal | None = Field(
        default=None, description="Monetary risk denominator used for R-multiples"
    )
    gross_R: Decimal | None = Field(
        default=None, description="Gross R-multiple (gross_pnl / risk_amount)"
    )
    net_R: Decimal | None = Field(
        default=None, description="Net R-multiple (net_pnl / risk_amount)"
    )
    calculation_version: str = Field(description="Engine revision version")
    timestamp: datetime = Field(description="UTC evaluation timestamp")

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise CostValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v
