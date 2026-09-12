"""
Unit tests for AlphaForge AuditEvent, AuditEventType, and LedgerVerificationResult models.
Verifies immutability, UTC timestamp enforcement, hash formatting, and strict validation.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import LedgerIntegrityError
from alphaforge.ledger.models import (
    GENESIS_PREVIOUS_HASH,
    AuditEvent,
    AuditEventType,
    LedgerVerificationResult,
)


def test_audit_event_type_taxonomy_coverage() -> None:
    """Verify all 22 authoritative event types are present in AuditEventType."""
    expected_events = {
        # Strategy
        "SIGNAL_GENERATED",
        "SIGNAL_REJECTED",
        # Risk
        "RISK_CHECK",
        "RISK_REJECTED",
        "RISK_RESERVED",
        "RISK_RELEASED",
        "CIRCUIT_BREAKER_TRIGGERED",
        # Order
        "ORDER_CREATED",
        "ORDER_VALIDATED",
        "ORDER_SUBMITTED",
        "ORDER_ACKNOWLEDGED",
        "ORDER_PARTIAL_FILL",
        "ORDER_FILLED",
        "ORDER_CANCELLED",
        "ORDER_REJECTED",
        "ORDER_UNKNOWN",
        # Protection
        "PROTECTION_PENDING",
        "PROTECTION_CONFIRMED",
        "PROTECTION_UNCONFIRMED",
        "PROTECTION_HAZARD",
        "EMERGENCY_PROTECTION_TRIGGERED",
        # Reconciliation
        "RECONCILIATION_STARTED",
        "RECONCILIATION_MATCHED",
        "RECONCILIATION_MISMATCH",
        "RECONCILIATION_FAILED",
        "MANUAL_ESCALATION",
    }
    actual_events = {e.value for e in AuditEventType}
    assert expected_events.issubset(actual_events)


def test_audit_event_creation_genesis() -> None:
    """Verify successful creation of a genesis audit event."""
    now = datetime.now(UTC)
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"quantity": 50},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="a" * 64,
        schema_version=1,
    )
    assert event.sequence_number == 1
    assert event.previous_event_hash == "GENESIS"
    assert event.event_hash == "a" * 64


def test_audit_event_creation_chained() -> None:
    """Verify successful creation of an event chained from a previous 64-char hex hash."""
    now = datetime.now(UTC)
    prev_hash = "b" * 64
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234568",
        sequence_number=2,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_VALIDATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"validated": True},
        previous_event_hash=prev_hash,
        event_hash="c" * 64,
        schema_version=1,
    )
    assert event.sequence_number == 2
    assert event.previous_event_hash == prev_hash


def test_audit_event_immutability() -> None:
    """Verify AuditEvent is strictly frozen against mutation."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"test": 1},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="f" * 64,
        schema_version=1,
    )
    with pytest.raises(ValidationError):
        event.sequence_number = 2


def test_audit_event_rejects_non_utc_timestamp() -> None:
    """Verify timestamps without UTC timezone are rejected."""
    naive_dt = datetime(2026, 9, 12, 12, 0, 0)
    with pytest.raises(LedgerIntegrityError, match="must be timezone-aware UTC"):
        AuditEvent(
            event_id="EVT-0123456789ABCDEF01234567",
            sequence_number=1,
            event_timestamp=naive_dt,
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-1",
            correlation_id="CORR-1",
            causation_id="CAUS-1",
            payload={},
            previous_event_hash=GENESIS_PREVIOUS_HASH,
            event_hash="f" * 64,
            schema_version=1,
        )


def test_audit_event_rejects_non_zero_offset_timestamp() -> None:
    """Verify non-UTC offset timestamps (e.g. +05:30) are rejected if not converted."""
    from datetime import timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    ist_dt = datetime(2026, 9, 12, 12, 0, 0, tzinfo=ist)
    with pytest.raises(LedgerIntegrityError, match="must be timezone-aware UTC"):
        AuditEvent(
            event_id="EVT-0123456789ABCDEF01234567",
            sequence_number=1,
            event_timestamp=ist_dt,
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-1",
            correlation_id="CORR-1",
            causation_id="CAUS-1",
            payload={},
            previous_event_hash=GENESIS_PREVIOUS_HASH,
            event_hash="f" * 64,
            schema_version=1,
        )


def test_audit_event_rejects_invalid_hashes() -> None:
    """Verify non-hex or improper length hashes are rejected."""
    now = datetime.now(UTC)
    with pytest.raises(LedgerIntegrityError, match="64-char hex digest"):
        AuditEvent(
            event_id="EVT-0123456789ABCDEF01234567",
            sequence_number=1,
            event_timestamp=now,
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-1",
            correlation_id="CORR-1",
            causation_id="CAUS-1",
            payload={},
            previous_event_hash="NOT_GENESIS_AND_NOT_HEX",
            event_hash="a" * 64,
            schema_version=1,
        )

    with pytest.raises(LedgerIntegrityError, match="64-char lowercase hex digest"):
        AuditEvent(
            event_id="EVT-0123456789ABCDEF01234567",
            sequence_number=1,
            event_timestamp=now,
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-1",
            correlation_id="CORR-1",
            causation_id="CAUS-1",
            payload={},
            previous_event_hash=GENESIS_PREVIOUS_HASH,
            event_hash="tooshort",
            schema_version=1,
        )


def test_audit_event_rejects_empty_identifiers() -> None:
    """Verify empty string identifiers are rejected."""
    now = datetime.now(UTC)
    with pytest.raises(LedgerIntegrityError, match="non-empty"):
        AuditEvent(
            event_id="   ",
            sequence_number=1,
            event_timestamp=now,
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-1",
            correlation_id="CORR-1",
            causation_id="CAUS-1",
            payload={},
            previous_event_hash=GENESIS_PREVIOUS_HASH,
            event_hash="a" * 64,
            schema_version=1,
        )


def test_ledger_verification_result_model() -> None:
    """Verify construction and immutability of LedgerVerificationResult."""
    res = LedgerVerificationResult(
        valid=True,
        event_count=10,
        first_sequence=1,
        last_sequence=10,
        verified_through_sequence=10,
    )
    assert res.valid is True
    assert res.event_count == 10
    assert res.corruption_detected is False
    with pytest.raises(ValidationError):
        res.valid = False
