"""
AlphaForge Immutable Audit Ledger Domain Models.
Defines immutable AuditEvent, AuditEventType taxonomy, and LedgerVerificationResult.
Enforces strict schema validation, timezone-aware UTC timestamps, and cryptographic integrity.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.core.exceptions import LedgerIntegrityError

GENESIS_PREVIOUS_HASH = "GENESIS"
_HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuditEventType(StrEnum):
    """
    Authoritative controlled event taxonomy for execution-critical audit records.
    """

    # Strategy events
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"

    # Risk events
    RISK_CHECK = "RISK_CHECK"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_RESERVED = "RISK_RESERVED"
    RISK_RELEASED = "RISK_RELEASED"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"

    # Order lifecycle events
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_VALIDATED = "ORDER_VALIDATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_PARTIAL_FILL = "ORDER_PARTIAL_FILL"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"

    # Protection events
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTION_CONFIRMED = "PROTECTION_CONFIRMED"
    PROTECTION_UNCONFIRMED = "PROTECTION_UNCONFIRMED"
    PROTECTION_HAZARD = "PROTECTION_HAZARD"
    EMERGENCY_PROTECTION_TRIGGERED = "EMERGENCY_PROTECTION_TRIGGERED"

    # Reconciliation events
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_MATCHED = "RECONCILIATION_MATCHED"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    MANUAL_ESCALATION = "MANUAL_ESCALATION"


class AuditEvent(BaseModel):
    """
    Immutable audit record in the append-only cryptographic hash chain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(description="Deterministic logical event identifier")
    sequence_number: int = Field(ge=1, description="Strictly increasing 1-indexed ledger sequence")
    event_timestamp: datetime = Field(description="Timezone-aware UTC timestamp of the event")
    event_type: AuditEventType = Field(description="Categorized audit event type")
    entity_type: str = Field(description="Domain entity category, e.g. ORDER, POSITION, SIGNAL")
    entity_id: str = Field(description="Unique domain entity identifier")
    correlation_id: str = Field(description="Forensic grouping identifier for logical lifecycle")
    causation_id: str = Field(description="Identifier of causal predecessor event or trigger")
    payload: dict[str, Any] = Field(description="Deterministic event payload dictionary")
    previous_event_hash: str = Field(description="SHA-256 hash of preceding event, or 'GENESIS'")
    event_hash: str = Field(description="SHA-256 canonical hash of this ledger entry")
    schema_version: int = Field(default=1, ge=1, description="Ledger event schema revision")

    @field_validator("event_id", "entity_type", "entity_id", "correlation_id", "causation_id")
    @classmethod
    def validate_non_empty_stripped(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise LedgerIntegrityError("Field must be non-empty string")
        return clean

    @field_validator("event_timestamp")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise LedgerIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @field_validator("previous_event_hash")
    @classmethod
    def validate_previous_hash(cls, v: str) -> str:
        clean = v.strip()
        if clean == GENESIS_PREVIOUS_HASH:
            return clean
        clean_lower = clean.lower()
        if not _HEX_64_PATTERN.match(clean_lower):
            raise LedgerIntegrityError(
                f"previous_event_hash must be 'GENESIS' or 64-char hex digest, got: '{v}'"
            )
        return clean_lower

    @field_validator("event_hash")
    @classmethod
    def validate_event_hash(cls, v: str) -> str:
        clean = v.strip().lower()
        if not _HEX_64_PATTERN.match(clean):
            raise LedgerIntegrityError(
                f"event_hash must be 64-char lowercase hex digest, got: '{v}'"
            )
        return clean


class LedgerVerificationResult(BaseModel):
    """
    Immutable structured result of a complete ledger hash chain verification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    valid: bool = Field(description="Whether the entire hash chain is cryptographically valid")
    event_count: int = Field(ge=0, description="Total verified event count")
    first_sequence: int | None = Field(default=None, description="Sequence number of first event")
    last_sequence: int | None = Field(default=None, description="Sequence number of last event")
    corruption_detected: bool = Field(
        default=False, description="Whether any corruption was detected"
    )
    corruption_sequence: int | None = Field(
        default=None, description="Sequence number where corruption occurred"
    )
    corruption_event_id: str | None = Field(
        default=None, description="Event ID at corruption point"
    )
    error_code: str | None = Field(default=None, description="Machine-readable error category")
    error_message: str | None = Field(
        default=None, description="Human-readable explanation of failure"
    )
    verified_through_sequence: int | None = Field(
        default=None, description="Last contiguous valid sequence number verified before corruption"
    )
