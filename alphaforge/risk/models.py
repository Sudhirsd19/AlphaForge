"""
AlphaForge Risk Engine Canonical Data Models.
Enforces strict immutability (frozen=True), Decimal precision, and UTC timestamp governance.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import RiskValidationError
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide


class CorrelatedGroupConfig(BaseModel):
    """
    Deterministic configuration for a group of correlated symbols.
    Limits aggregate portfolio risk exposure across grouped instruments.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    group_id: str = Field(description="Unique uppercase identifier for the correlation group")
    symbols: tuple[str, ...] = Field(description="Tuple of uppercase symbols in this group")
    max_group_risk: Decimal = Field(
        gt=Decimal("0"),
        lt=Decimal("1"),
        description=(
            "Maximum aggregate risk percentage allowed for this group (e.g. 0.0150 for 1.5%)"
        ),
    )

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise RiskValidationError(f"group_id must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise RiskValidationError("symbols tuple must not be empty")
        cleaned = tuple(s.strip().upper() for s in v)
        for s in cleaned:
            if not s:
                raise RiskValidationError("symbol in correlation group cannot be empty")
        return cleaned


class RiskConfig(BaseModel):
    """
    Authoritative V1 parameter configuration for the AlphaForge Risk Engine.
    All percentage and monetary parameters strictly use fixed-point Decimal arithmetic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_risk_per_trade: Decimal = Field(
        default=Decimal("0.0050"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Maximum risk per single trade as a fraction of equity (0.50%)",
    )
    max_portfolio_risk: Decimal = Field(
        default=Decimal("0.0200"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Maximum aggregate open risk as a fraction of equity (2.00%)",
    )
    max_single_position_notional: Decimal = Field(
        default=Decimal("0.20"),
        gt=Decimal("0"),
        description="Maximum notional value of a single position as a fraction of equity (20%)",
    )
    max_portfolio_notional: Decimal = Field(
        default=Decimal("1.00"),
        gt=Decimal("0"),
        description="Maximum aggregate notional exposure as a fraction of equity (100%)",
    )
    max_open_trades: int = Field(
        default=5,
        ge=1,
        description="Maximum number of concurrent open trades and active reservations",
    )
    daily_loss_soft_limit: Decimal = Field(
        default=Decimal("0.0200"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Daily loss threshold at which risk throttling activates (2.00%)",
    )
    daily_loss_hard_limit: Decimal = Field(
        default=Decimal("0.0300"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Daily loss threshold at which all new trading halts (3.00%)",
    )
    risk_reserve_buffer: Decimal = Field(
        default=Decimal("0.05"),
        ge=Decimal("0"),
        lt=Decimal("1"),
        description="Buffer percentage added to capital/collateral requirements (5%)",
    )
    minimum_stop_distance: Decimal = Field(
        default=Decimal("0.0010"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Minimum stop distance as a fraction of entry price (0.10%)",
    )
    maximum_stop_distance: Decimal = Field(
        default=Decimal("0.0300"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Maximum stop distance as a fraction of entry price (3.00%)",
    )
    correlated_groups: tuple[CorrelatedGroupConfig, ...] = Field(
        default=(),
        description="Deterministic correlation exposure groups",
    )
    market_timezone: str = Field(
        default="Asia/Kolkata",
        description="Canonical business market timezone for daily risk reset",
    )
    allow_lot_flooring: bool = Field(
        default=False,
        description=(
            "When True, automated sizing explicitly floors raw quantity to whole lot multiples. "
            "When False (default), non-exact quantity is rejected."
        ),
    )
    calculation_version: int = Field(
        default=1,
        ge=1,
        description="Risk calculation engine version",
    )

    @model_validator(mode="after")
    def validate_config_invariants(self) -> "RiskConfig":
        """Verify internal consistency of risk configuration limits."""
        if self.daily_loss_hard_limit <= self.daily_loss_soft_limit:
            raise RiskValidationError(
                f"daily_loss_hard_limit ({self.daily_loss_hard_limit}) must be > "
                f"daily_loss_soft_limit ({self.daily_loss_soft_limit})"
            )
        if self.maximum_stop_distance <= self.minimum_stop_distance:
            raise RiskValidationError(
                f"maximum_stop_distance ({self.maximum_stop_distance}) must be > "
                f"minimum_stop_distance ({self.minimum_stop_distance})"
            )
        if self.max_portfolio_risk < self.max_risk_per_trade:
            raise RiskValidationError(
                f"max_portfolio_risk ({self.max_portfolio_risk}) must be >= "
                f"max_risk_per_trade ({self.max_risk_per_trade})"
            )
        return self


class RiskInput(BaseModel):
    """
    Complete context of a proposed trade evaluated by the Risk Engine.
    All monetary and price fields use fixed-point Decimal arithmetic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    signal_id: str = Field(description="Deterministic signal hash identifier")
    symbol: str = Field(description="Underlying trading symbol (e.g. NIFTY)")
    side: TradeSide = Field(description="LONG | SHORT")
    entry_price: Decimal = Field(description="Proposed entry reference price")
    stop_price: Decimal = Field(description="Proposed stop-loss reference price")
    contract_id: str = Field(description="Specific instrument/contract identifier")
    lot_size: int = Field(description="Exchange contract lot size")
    contract_multiplier: Decimal = Field(
        description="Monetary value per point per unit quantity",
    )
    account_equity: Decimal = Field(description="Current total account equity")
    available_capital: Decimal = Field(description="Free unencumbered capital/margin")
    proposed_quantity: int | None = Field(
        default=None,
        description="Optional explicitly proposed quantity; if None, engine determines size",
    )
    collateral_required: Decimal | None = Field(
        default=None,
        description="Optional explicit margin/collateral requirement; if None, buffer formula used",
    )
    evaluation_timestamp: datetime = Field(description="Evaluation context UTC timestamp")
    calculation_version: int = Field(default=1, description="Engine revision")

    @field_validator("signal_id", "symbol", "contract_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise RiskValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("evaluation_timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise RiskValidationError(f"evaluation_timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_finiteness(self) -> "RiskInput":
        """Ensure numeric inputs are finite Decimals."""
        for field_name in (
            "entry_price",
            "stop_price",
            "contract_multiplier",
            "account_equity",
            "available_capital",
        ):
            val: Decimal = getattr(self, field_name)
            if not val.is_finite() or val.is_nan():
                raise RiskValidationError(f"{field_name} must be finite Decimal: {val}")
        if self.collateral_required is not None and (
            not self.collateral_required.is_finite() or self.collateral_required.is_nan()
        ):
            raise RiskValidationError("collateral_required must be finite Decimal")
        return self


class RiskReservation(BaseModel):
    """
    Logical, in-memory reservation of portfolio risk budget for an approved trade.
    This is NOT an order and sends nothing to any external venue.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    reservation_id: str = Field(description="Unique deterministic reservation identifier")
    signal_id: str = Field(description="Associated strategy signal identifier")
    symbol: str = Field(description="Underlying instrument symbol")
    side: TradeSide = Field(description="LONG | SHORT")
    entry_price: Decimal = Field(description="Approved entry reference price")
    stop_price: Decimal = Field(description="Approved stop-loss reference price")
    quantity: int = Field(gt=0, description="Approved trade quantity in units")
    monetary_risk: Decimal = Field(gt=Decimal("0"), description="Total monetary risk of position")
    notional: Decimal = Field(gt=Decimal("0"), description="Total notional value of position")
    created_timestamp: datetime = Field(description="UTC timestamp when reservation was granted")
    calculation_version: int = Field(default=1, description="Engine revision")

    @field_validator("reservation_id", "signal_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise RiskValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("created_timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise RiskValidationError(f"created_timestamp must be timezone-aware UTC: {v}")
        return v


class PortfolioRiskState(BaseModel):
    """
    Snapshot of current portfolio risk utilization prior to evaluating a new trade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_equity: Decimal = Field(description="Current account equity")
    available_capital: Decimal = Field(description="Current available capital")
    open_trade_count: int = Field(default=0, ge=0, description="Active position count")
    reserved_risk: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Aggregate monetary risk of active positions/reservations",
    )
    reserved_notional: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Aggregate notional value of active positions/reservations",
    )
    daily_starting_equity: Decimal = Field(description="Equity at start of current trading day")
    current_equity: Decimal = Field(description="Current live equity")
    active_reservations: tuple[RiskReservation, ...] = Field(
        default=(),
        description="Tuple of active risk reservations currently held",
    )


class DailyRiskState(BaseModel):
    """
    Deterministic tracking of daily trading loss relative to starting equity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    daily_starting_equity: Decimal = Field(gt=Decimal("0"), description="Day start equity")
    current_equity: Decimal = Field(description="Current live equity")
    daily_loss: Decimal = Field(ge=Decimal("0"), description="max(0, starting - current)")
    daily_loss_pct: Decimal = Field(ge=Decimal("0"), description="daily_loss / starting_equity")
    is_soft_limit_breached: bool = Field(description="True if loss >= soft limit")
    is_hard_limit_breached: bool = Field(description="True if loss >= hard limit")


class RiskDecision(BaseModel):
    """
    Immutable audit record representing the definitive risk evaluation for a trade proposal.
    Provides complete traceability for every decision without external side-effects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision: RiskDecisionState = Field(description="APPROVED | REJECTED | INVALID")
    reason_code: RiskReasonCode = Field(description="Deterministic machine-readable reason")
    reason: str = Field(description="Human-readable audit explanation")
    signal_id: str = Field(description="Associated signal ID")
    symbol: str = Field(description="Underlying trading symbol")
    side: TradeSide = Field(description="LONG | SHORT")
    equity: Decimal | None = Field(default=None, description="Account equity evaluated")
    entry_price: Decimal | None = Field(default=None, description="Entry price evaluated")
    stop_price: Decimal | None = Field(default=None, description="Stop price evaluated")
    risk_distance: Decimal | None = Field(default=None, description="abs(entry - stop)")
    risk_amount: Decimal | None = Field(default=None, description="Total monetary risk approved")
    quantity: int | None = Field(default=None, description="Approved trade quantity")
    notional: Decimal | None = Field(default=None, description="Approved position notional value")
    portfolio_risk_before: Decimal | None = Field(
        default=None, description="Portfolio risk ratio before"
    )
    portfolio_risk_after: Decimal | None = Field(
        default=None, description="Portfolio risk ratio after"
    )
    daily_loss: Decimal | None = Field(default=None, description="Current daily monetary loss")
    calculation_version: int = Field(default=1, description="Engine revision")
    timestamp: datetime = Field(description="UTC evaluation timestamp")

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise RiskValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v
