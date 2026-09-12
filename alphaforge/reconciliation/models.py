"""
AlphaForge Reconciliation Domain Models.
Defines immutable data models for reconciliation results, comparison records,
status enums, and deterministic reason codes.
All timestamps enforce timezone-aware UTC.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.broker.models import BrokerOrderStatus
from alphaforge.core.exceptions import OrderValidationError
from alphaforge.execution.enums import OrderState


class ReconciliationStatus(StrEnum):
    """
    High-level outcome status of a state reconciliation pass.
    """

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class ReconciliationReasonCode(StrEnum):
    """
    Deterministic reason code classifying the exact outcome of state reconciliation.
    """

    MATCHED = "MATCHED"
    LOCAL_ORDER_MISSING = "LOCAL_ORDER_MISSING"
    BROKER_ORDER_MISSING = "BROKER_ORDER_MISSING"
    UNKNOWN_EXTERNAL_ORDER = "UNKNOWN_EXTERNAL_ORDER"
    POSITION_MATCHED = "POSITION_MATCHED"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    UNKNOWN_EXTERNAL_POSITION = "UNKNOWN_EXTERNAL_POSITION"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    INVALID_LOCAL_STATE = "INVALID_LOCAL_STATE"
    INVALID_BROKER_STATE = "INVALID_BROKER_STATE"
    PROTECTION_UNCONFIRMED = "PROTECTION_UNCONFIRMED"
    MANUAL_ESCALATION_REQUIRED = "MANUAL_ESCALATION_REQUIRED"
    RECOVERY_FAILED = "RECOVERY_FAILED"


class ReconciliationAction(StrEnum):
    """
    Deterministic action applied to local FSM during reconciliation.
    """

    NONE = "NONE"
    SYNC_ACKNOWLEDGED = "SYNC_ACKNOWLEDGED"
    SYNC_PARTIAL_FILL = "SYNC_PARTIAL_FILL"
    SYNC_FULL_FILL = "SYNC_FULL_FILL"
    SYNC_CANCELLED = "SYNC_CANCELLED"
    SYNC_CLOSED = "SYNC_CLOSED"
    TRIGGER_EMERGENCY_PROTECTION = "TRIGGER_EMERGENCY_PROTECTION"
    ESCALATE_MANUAL = "ESCALATE_MANUAL"


class OrderReconciliationRecord(BaseModel):
    """
    Immutable audit record of reconciling an individual order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    client_order_id: str = Field(description="Client order identifier")
    broker_order_id: str | None = Field(
        default=None, description="Broker-assigned order identifier"
    )
    local_state: OrderState | None = Field(default=None, description="Local Phase 7 order state")
    broker_status: BrokerOrderStatus | None = Field(
        default=None, description="Broker-reported status"
    )
    local_quantity: int | None = Field(default=None, ge=0, description="Local expected quantity")
    broker_filled_quantity: int | None = Field(
        default=None, ge=0, description="Broker executed quantity"
    )
    action: ReconciliationAction = Field(description="Action applied to local order")
    reason_code: ReconciliationReasonCode = Field(description="Deterministic reason code")
    notes: str = Field(default="", description="Human-readable audit notes")

    @field_validator("client_order_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean


class PositionReconciliationRecord(BaseModel):
    """
    Immutable audit record of reconciling an individual position.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    position_id: str = Field(description="Position identifier")
    symbol: str = Field(description="Instrument symbol")
    local_quantity: int = Field(ge=0, description="Local net position quantity")
    broker_quantity: int = Field(ge=0, description="Broker net position quantity")
    action: ReconciliationAction = Field(description="Action applied to local position")
    reason_code: ReconciliationReasonCode = Field(description="Deterministic reason code")
    is_protection_confirmed: bool = Field(
        default=False, description="Whether resting stop protection is confirmed"
    )
    notes: str = Field(default="", description="Human-readable audit notes")

    @field_validator("position_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean


class ReconciliationResult(BaseModel):
    """
    Immutable, structured result summarizing a cold-boot or runtime reconciliation cycle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    reconciliation_id: str = Field(description="Deterministic unique reconciliation pass ID")
    timestamp: datetime = Field(description="UTC evaluation timestamp")
    status: ReconciliationStatus = Field(description="Overall reconciliation outcome status")
    local_order_count: int = Field(ge=0, description="Count of open local orders examined")
    broker_order_count: int = Field(ge=0, description="Count of open broker orders examined")
    local_position_count: int = Field(ge=0, description="Count of open local positions examined")
    broker_position_count: int = Field(ge=0, description="Count of open broker positions examined")
    matched_count: int = Field(ge=0, description="Count of successfully matched records")
    mismatch_count: int = Field(ge=0, description="Count of conflicting records")
    unknown_count: int = Field(ge=0, description="Count of unrecognised / external records")
    new_entries_allowed: bool = Field(
        description="True ONLY if reconciliation is completely MATCHED and safe"
    )
    manual_escalation_required: bool = Field(
        description="True if human operator intervention is mandated"
    )
    reason_code: ReconciliationReasonCode = Field(description="Deterministic reason code")
    order_details: tuple[OrderReconciliationRecord, ...] = Field(
        default=(), description="Detailed per-order comparison records"
    )
    position_details: tuple[PositionReconciliationRecord, ...] = Field(
        default=(), description="Detailed per-position comparison records"
    )

    @field_validator("timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"Timestamp must be timezone-aware UTC: {v}")
        return v
