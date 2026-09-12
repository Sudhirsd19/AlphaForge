"""
AlphaForge Execution & Order Lifecycle Domain Models.
Defines immutable data models for transition events, results, emergency exit commands,
and watchdog safety decisions.
All timestamps enforce timezone-aware UTC.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import OrderValidationError
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderSide,
    OrderState,
    WatchdogStatus,
)
from alphaforge.risk.enums import TradeSide


class TransitionEvent(BaseModel):
    """
    Immutable record of an individual state machine transition.
    Provides complete audit lineage for order lifecycle mutations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: str = Field(description="Unique order identifier")
    previous_state: OrderState = Field(description="State before transition")
    new_state: OrderState = Field(description="State after transition")
    event: str = Field(description="Event triggering the transition")
    reason_code: ExecutionReasonCode = Field(description="Deterministic reason code")
    reason: str = Field(description="Human-readable audit explanation")
    timestamp: datetime = Field(description="UTC timestamp of transition event")
    signal_id: str | None = Field(
        default=None, description="Optional associated strategy signal ID"
    )
    symbol: str | None = Field(default=None, description="Optional instrument symbol")
    position_id: str | None = Field(default=None, description="Optional associated position ID")
    filled_quantity: int | None = Field(
        default=None, ge=0, description="Quantity filled in this event"
    )
    remaining_quantity: int | None = Field(
        default=None, ge=0, description="Quantity remaining after fill"
    )
    average_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Average execution price for fill events"
    )

    @field_validator("order_id", "signal_id", "symbol", "position_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v


class TransitionResult(BaseModel):
    """
    Immutable result of attempting a state machine transition.
    Indicates whether the transition was accepted, rejected, or deduplicated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    success: bool = Field(description="True if transition was applied or safely deduplicated")
    order_id: str = Field(description="Target order identifier")
    previous_state: OrderState = Field(description="State before transition attempt")
    current_state: OrderState = Field(description="Current authoritative state after operation")
    event: str = Field(description="Event attempted")
    reason_code: ExecutionReasonCode = Field(
        description="Deterministic machine-readable reason code"
    )
    reason: str = Field(description="Human-readable explanation")
    transition_event: TransitionEvent | None = Field(
        default=None, description="Event record if state mutated; None on reject/dedup"
    )
    timestamp: datetime = Field(description="UTC timestamp of evaluation")

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v


class EmergencyExitCommand(BaseModel):
    """
    Broker-independent immutable command to liquidate an unprotected position immediately.
    Contains NO network, broker, or execution behavior; represents pure intent for execution layer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    command_id: str = Field(description="Unique deterministic command identifier")
    order_id: str = Field(description="Associated order identifier")
    position_id: str = Field(description="Associated position identifier")
    symbol: str = Field(description="Underlying trading symbol")
    side: OrderSide = Field(description="Exit order side: SELL for LONG position, BUY for SHORT")
    position_side: TradeSide = Field(description="Side of original position being liquidated")
    quantity: int = Field(gt=0, description="Exact quantity to liquidate (never 0, never guessed)")
    reason: str = Field(description="Trigger reason for emergency liquidation")
    created_at: datetime = Field(description="UTC generation timestamp")

    @field_validator("command_id", "order_id", "position_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("created_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"created_at must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_directional_safety(self) -> "EmergencyExitCommand":
        """Verify strict side-awareness: LONG exits via SELL, SHORT exits via BUY."""
        if self.position_side == TradeSide.LONG and self.side != OrderSide.SELL:
            raise OrderValidationError(
                f"Directional safety violation: LONG position must exit via SELL, got {self.side}"
            )
        if self.position_side == TradeSide.SHORT and self.side != OrderSide.BUY:
            raise OrderValidationError(
                f"Directional safety violation: SHORT position must exit via BUY, got {self.side}"
            )
        if self.quantity <= 0:
            raise OrderValidationError(f"Emergency exit quantity must be positive: {self.quantity}")
        return self


class WatchdogDecision(BaseModel):
    """
    Immutable decision rendered by the Emergency Protection Watchdog.
    Identifies whether a position is confirmed protected or in an unprotected hazard state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: WatchdogStatus = Field(
        description="SAFE | UNPROTECTED_HAZARD | TIMEOUT_BREACH | ESCALATED"
    )
    order_id: str = Field(description="Audited order identifier")
    current_state: OrderState = Field(description="Current order state")
    is_protected: bool = Field(description="True only if confirmed stop protection is verified")
    is_emergency: bool = Field(description="True if emergency liquidation protocol is active")
    reason_code: ExecutionReasonCode = Field(description="Deterministic reason code")
    reason: str = Field(description="Human-readable decision explanation")
    emergency_command: EmergencyExitCommand | None = Field(
        default=None, description="Liquidation command if emergency was triggered"
    )
    timestamp: datetime = Field(description="UTC evaluation timestamp")

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"timestamp must be timezone-aware UTC: {v}")
        return v


class Order(BaseModel):
    """
    Immutable snapshot representation of an order managed by the state machine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: str = Field(description="Unique immutable order identifier")
    state: OrderState = Field(description="Current authoritative state")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="Position trade side: LONG | SHORT")
    quantity: int = Field(gt=0, description="Intended total order quantity in units")
    filled_quantity: int = Field(default=0, ge=0, description="Cumulative executed quantity")
    signal_id: str | None = Field(default=None, description="Associated strategy signal identifier")
    position_id: str | None = Field(default=None, description="Associated position identifier")
    is_protection_confirmed: bool = Field(
        default=False, description="True ONLY when resting stop protection is explicitly confirmed"
    )
    protection_order_id: str | None = Field(
        default=None, description="External/internal ID of resting stop-loss order"
    )
    filled_at: datetime | None = Field(default=None, description="UTC timestamp of full/first fill")
    created_at: datetime = Field(description="UTC creation timestamp")
    updated_at: datetime = Field(description="UTC last state mutation timestamp")

    @field_validator("order_id", "symbol", "signal_id", "position_id", "protection_order_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("created_at", "updated_at", "filled_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"Timestamp must be timezone-aware UTC: {v}")
        return v
