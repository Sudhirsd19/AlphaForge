"""Immutable Phase 6 cost/slippage data models."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.risk.enums import TradeSide


class CostConfig(BaseModel):
    """Explicit, immutable Phase 6 cost assumptions.

    Zero-valued defaults are modeling assumptions only and are not broker-verified tariffs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    entry_fee_rate: Decimal = Field(default=Decimal("0"), description="Decimal fraction")
    exit_fee_rate: Decimal = Field(default=Decimal("0"), description="Decimal fraction")
    entry_slippage_rate: Decimal = Field(default=Decimal("0"), description="Decimal fraction")
    exit_slippage_rate: Decimal = Field(default=Decimal("0"), description="Decimal fraction")
    fixed_cost_per_trade: Decimal = Field(
        default=Decimal("0"),
        description="Round-trip fixed cost, charged exactly once",
    )

    @field_validator(
        "entry_fee_rate",
        "exit_fee_rate",
        "entry_slippage_rate",
        "exit_slippage_rate",
        "fixed_cost_per_trade",
    )
    @classmethod
    def validate_non_negative_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < Decimal("0"):
            raise ValueError("cost configuration values must be finite and non-negative Decimals")
        return value


class CostInput(BaseModel):
    """Explicit reference prices and risk context required for deterministic calculation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    side: TradeSide
    entry_reference_price: Decimal
    exit_reference_price: Decimal
    quantity: int
    contract_multiplier: Decimal
    risk_amount: Decimal
    cost_config: CostConfig
    timestamp: datetime
    symbol: str | None = None
    signal_id: str | None = None

    @field_validator(
        "entry_reference_price",
        "exit_reference_price",
        "contract_multiplier",
        "risk_amount",
    )
    @classmethod
    def validate_positive_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError("required numeric input must be a positive finite Decimal")
        return value

    @field_validator("quantity")
    @classmethod
    def validate_positive_quantity(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("quantity must be strictly positive")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be explicitly timezone-aware UTC")
        return value

    @field_validator("symbol", "signal_id")
    @classmethod
    def validate_optional_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional identifiers must be non-empty when provided")
        return value


class CostResult(BaseModel):
    """Immutable complete Phase 6 calculation result."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    side: TradeSide
    reference_entry_price: Decimal
    effective_entry_price: Decimal
    reference_exit_price: Decimal
    effective_exit_price: Decimal
    quantity: int
    contract_multiplier: Decimal
    entry_notional: Decimal
    exit_notional: Decimal
    entry_slippage_cost: Decimal
    exit_slippage_cost: Decimal
    total_slippage_cost: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    fixed_cost: Decimal
    transaction_cost: Decimal
    total_round_trip_cost: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    risk_amount: Decimal
    gross_R: Decimal
    net_R: Decimal
    calculation_version: str
    timestamp: datetime
    symbol: str | None = None
    signal_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be explicitly timezone-aware UTC")
        return value
