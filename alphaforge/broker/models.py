"""
AlphaForge Broker Abstraction Domain Models.
Defines immutable models for broker orders, requests, statuses, and positions.
All timestamps enforce timezone-aware UTC.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import OrderValidationError
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.risk.enums import TradeSide


class BrokerOrderStatus(StrEnum):
    """
    Authoritative broker execution status of an order on the exchange/broker order book.
    """

    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class BrokerOrderType(StrEnum):
    """Order type accepted by broker interface."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"


class BrokerOrderRequest(BaseModel):
    """
    Immutable request submitted to the broker abstraction layer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    client_order_id: str = Field(description="Deterministic client-generated identifier")
    symbol: str = Field(description="Trading symbol")
    side: OrderSide = Field(description="Execution direction: BUY | SELL")
    quantity: int = Field(gt=0, description="Intended order quantity")
    role: OrderRole = Field(description="Order role: ENTRY | STOP | EXIT")
    order_type: BrokerOrderType = Field(
        default=BrokerOrderType.MARKET, description="Order execution type"
    )
    price: Decimal | None = Field(default=None, gt=Decimal("0"), description="Limit price if LIMIT")
    trigger_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Trigger price if STOP_LOSS"
    )

    @field_validator("client_order_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean


class BrokerOrder(BaseModel):
    """
    Immutable snapshot of a broker-side order record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    broker_order_id: str = Field(description="Broker-assigned unique order ID")
    client_order_id: str = Field(description="Deterministic client order ID")
    symbol: str = Field(description="Trading symbol")
    side: OrderSide = Field(description="Execution direction: BUY | SELL")
    quantity: int = Field(gt=0, description="Total order quantity")
    role: OrderRole = Field(description="Order role: ENTRY | STOP | EXIT")
    order_type: BrokerOrderType = Field(description="Order type: MARKET | LIMIT | STOP_LOSS")
    status: BrokerOrderStatus = Field(description="Current broker execution status")
    filled_quantity: int = Field(default=0, ge=0, description="Cumulative executed quantity")
    average_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Volume-weighted execution price"
    )
    created_at: datetime = Field(description="UTC creation timestamp")
    updated_at: datetime = Field(description="UTC last update timestamp")

    @field_validator("broker_order_id", "client_order_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_filled_quantity_invariant(self) -> "BrokerOrder":
        if self.filled_quantity > self.quantity:
            raise OrderValidationError(
                f"filled_quantity ({self.filled_quantity}) cannot exceed quantity ({self.quantity})"
            )
        if self.status == BrokerOrderStatus.FILLED and self.filled_quantity != self.quantity:
            msg = (
                f"FILLED status requires filled_quantity == quantity "
                f"({self.filled_quantity} != {self.quantity})"
            )
            raise OrderValidationError(msg)
        return self


class BrokerPosition(BaseModel):
    """
    Immutable representation of an active broker-side position.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    position_id: str = Field(description="Unique position identifier")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="Position trade side: LONG | SHORT")
    quantity: int = Field(ge=0, description="Current net position quantity (0 = flat)")
    average_price: Decimal | None = Field(
        default=None, gt=Decimal("0"), description="Average entry price"
    )
    status: str = Field(default="OPEN", description="Position status: OPEN | FLAT")

    @field_validator("position_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean
