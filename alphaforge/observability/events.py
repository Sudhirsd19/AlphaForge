"""
AlphaForge Structured Observability Events.

Defines immutable, typed, secret-scrubbed diagnostic event models adhering to
forensic event taxonomy, deterministic event identity, and strict attributes JSON-safety.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.security.redaction import redact_text


class ObservabilitySeverity(StrEnum):
    """Severity levels for observability diagnostic events."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ObservabilityCategory(StrEnum):
    """Top-level taxonomy categories for observability events."""

    SYSTEM = "SYSTEM"
    DATA = "DATA"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    SECURITY = "SECURITY"
    ORDER = "ORDER"
    FILL = "FILL"
    POSITION = "POSITION"
    RECONCILIATION = "RECONCILIATION"
    RECOVERY = "RECOVERY"


# Canonical Event Types supported by Phase 14 taxonomy
class SystemEventType(StrEnum):
    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"
    RESTART = "RESTART"
    CONFIGURATION = "CONFIGURATION"
    HEALTH_STATUS = "HEALTH_STATUS"


class DataEventType(StrEnum):
    DATA_RECEIVED = "DATA_RECEIVED"
    DATA_GAP = "DATA_GAP"
    DATA_BACKFILL = "DATA_BACKFILL"
    STALE_DATA = "STALE_DATA"
    DATA_ERROR = "DATA_ERROR"


class StrategyEventType(StrEnum):
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_ACCEPTED = "SIGNAL_ACCEPTED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_FILTERED = "SIGNAL_FILTERED"


class RiskEventType(StrEnum):
    RISK_CHECK = "RISK_CHECK"
    RISK_ACCEPTED = "RISK_ACCEPTED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_RESERVED = "RISK_RESERVED"
    RISK_RELEASED = "RISK_RELEASED"
    DAILY_LIMIT_TRIGGERED = "DAILY_LIMIT_TRIGGERED"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"


class SecurityEventType(StrEnum):
    SECURITY_CHECK = "SECURITY_CHECK"
    SECURITY_REJECTED = "SECURITY_REJECTED"
    STARTUP_SECURITY_FAILED = "STARTUP_SECURITY_FAILED"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    KILL_SWITCH_DISENGAGED = "KILL_SWITCH_DISENGAGED"


class OrderEventType(StrEnum):
    ORDER_INTENT = "ORDER_INTENT"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_ACK = "ORDER_ACK"
    ORDER_REJECT = "ORDER_REJECT"
    ORDER_TIMEOUT = "ORDER_TIMEOUT"
    ORDER_RETRY = "ORDER_RETRY"
    ORDER_CANCEL = "ORDER_CANCEL"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"


class FillEventType(StrEnum):
    FILL_RECEIVED = "FILL_RECEIVED"
    FILL_RECONCILED = "FILL_RECONCILED"


class PositionEventType(StrEnum):
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_UPDATE = "POSITION_UPDATE"
    POSITION_CLOSE = "POSITION_CLOSE"


class ReconciliationEventType(StrEnum):
    RECONCILIATION_START = "RECONCILIATION_START"
    RECONCILIATION_SUCCESS = "RECONCILIATION_SUCCESS"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"


class RecoveryEventType(StrEnum):
    RECOVERY_START = "RECOVERY_START"
    RECOVERY_SUCCESS = "RECOVERY_SUCCESS"
    RECOVERY_FAILURE = "RECOVERY_FAILURE"


def _sanitize_attributes(val: Any) -> Any:
    """
    Recursively validate and sanitize attributes.
    Ensures strict JSON-compatible primitives and redacts sensitive strings.
    Rejects unsupported arbitrary objects.
    """
    if val is None or isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, str):
        return redact_text(val)
    if isinstance(val, (list, tuple)):
        return [_sanitize_attributes(item) for item in val]
    if isinstance(val, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Attribute dictionary key must be a string, got {type(k).__name__}"
                )
            sanitized_k = redact_text(k)
            sanitized_dict[sanitized_k] = _sanitize_attributes(v)
        return sanitized_dict

    raise TypeError(
        f"Unsupported attribute value of type {type(val).__name__}. "
        "Attributes only permit JSON-safe primitives (None, bool, int, float, str, list, dict)."
    )


def compute_deterministic_event_id(
    category: str,
    event_type: str,
    source: str = "alphaforge",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    client_order_id: str | None = None,
    symbol: str | None = None,
    sequence: int | None = None,
    extra_seed: str | None = None,
) -> str:
    """
    Derive deterministic event identity from stable domain identifiers.
    Never relies solely on an uncontrolled wall-clock timestamp.
    """
    components = [
        source.strip().lower(),
        category.strip().upper(),
        event_type.strip().upper(),
        (correlation_id or "").strip(),
        (causation_id or "").strip(),
        (client_order_id or "").strip(),
        (symbol or "").strip().upper(),
        str(sequence) if sequence is not None else "",
        (extra_seed or "").strip(),
    ]
    raw_key = ":".join(components).encode("utf-8")
    digest = hashlib.sha256(raw_key).hexdigest()[:16].upper()
    return f"OBS-{digest}"


class ObservabilityEvent(BaseModel):
    """
    Immutable, strictly typed, JSON-safe structured observability event.

    Invariants:
    - Immutable: frozen=True, extra="forbid"
    - Secret safety: message and all nested attribute strings are scrubbed via redact_text.
    - JSON safety: attributes only permit validated JSON-compatible primitives.
    - Deterministic ID: if event_id is empty, deterministically computed from stable fields.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    event_id: str = ""
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: ObservabilitySeverity = ObservabilitySeverity.INFO
    category: ObservabilityCategory
    source: str = "alphaforge"
    symbol: str | None = None
    mode: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    position_id: str | None = None
    message: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: str) -> str:
        return redact_text(v)

    @field_validator("attributes")
    @classmethod
    def _validate_attributes(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_attributes(v)  # type: ignore[no-any-return]

    @model_validator(mode="after")
    def _ensure_event_id_and_utc(self) -> ObservabilityEvent:
        # Guarantee timezone-aware UTC
        if self.timestamp.tzinfo is None:
            ts_utc = self.timestamp.replace(tzinfo=UTC)
        else:
            ts_utc = self.timestamp.astimezone(UTC)

        # Compute deterministic event_id if not provided
        eid = self.event_id
        if not eid or not eid.strip():
            eid = compute_deterministic_event_id(
                category=self.category.value
                if isinstance(self.category, ObservabilityCategory)
                else str(self.category),
                event_type=self.event_type,
                source=self.source,
                correlation_id=self.correlation_id,
                causation_id=self.causation_id,
                client_order_id=self.client_order_id,
                symbol=self.symbol,
                extra_seed=self.message,
            )

        if eid != self.event_id or ts_utc != self.timestamp:
            object.__setattr__(self, "event_id", eid)
            object.__setattr__(self, "timestamp", ts_utc)

        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert event to a standard Python dictionary with serialized enums and datetimes."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
            "category": self.category.value,
            "source": self.source,
            "symbol": self.symbol,
            "mode": self.mode,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "position_id": self.position_id,
            "message": self.message,
            "attributes": self.attributes,
        }

    def to_json(self) -> str:
        """Serialize event to a single-line compact JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)
