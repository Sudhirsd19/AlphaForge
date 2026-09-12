"""
Unit tests for AlphaForge AuditEvent, AuditEventType, and LedgerVerificationResult models.
Verifies immutability, UTC timestamp enforcement, hash formatting, and strict validation.
"""

import contextlib
import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import LedgerIntegrityError
from alphaforge.ledger.models import (
    GENESIS_PREVIOUS_HASH,
    AuditEvent,
    AuditEventType,
    FrozenDict,
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


def test_frozen_dict_all_mutating_methods_raise_type_error() -> None:
    """Verify all mutating dict operations on FrozenDict raise TypeError."""
    fd = FrozenDict({"a": 1, "b": 2})

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd["c"] = 3

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        del fd["a"]

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd.pop("a")

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd.popitem()

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd.clear()

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd.update({"a": 9})

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        fd.setdefault("k", 5)

    # Immutability preservation on copying
    assert fd.copy() is fd
    assert copy.copy(fd) is fd
    assert copy.deepcopy(fd) is fd


def test_audit_event_payload_top_level_mutation_raises_type_error() -> None:
    """Verify top-level payload cannot be mutated via __setitem__, __delitem__, pop, or clear."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"status": "OK", "code": 200},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="a" * 64,
        schema_version=1,
    )

    assert isinstance(event.payload, FrozenDict)

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        event.payload["status"] = "MUTATED"

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        event.payload["new_field"] = "LEAK"

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        del event.payload["code"]

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        event.payload.pop("code")

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        event.payload.clear()


def test_audit_event_payload_nested_dict_mutation_raises_type_error() -> None:
    """Verify deeply nested dicts within AuditEvent payload are also FrozenDict and immutable."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"outer": {"inner": {"deep_val": 42}}},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="a" * 64,
        schema_version=1,
    )

    outer = event.payload["outer"]
    assert isinstance(outer, FrozenDict)
    inner = outer["inner"]
    assert isinstance(inner, FrozenDict)
    assert inner["deep_val"] == 42

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        inner["deep_val"] = 99

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        inner["new_inner_key"] = "bad"

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        del inner["deep_val"]


def test_audit_event_payload_nested_list_is_tuple_and_mutation_fails() -> None:
    """Verify lists and sets inside payload are recursively converted to immutable tuples."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"items": [1, 2, [3, 4]], "tags": ["A", "B"]},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="a" * 64,
        schema_version=1,
    )

    items = event.payload["items"]
    assert isinstance(items, tuple)
    assert items[0] == 1
    assert items[1] == 2
    assert isinstance(items[2], tuple)
    assert items[2] == (3, 4)

    # Tuple has no append, mutating element raises TypeError
    with pytest.raises(AttributeError):
        items.append(5)  # type: ignore[attr-defined]

    with pytest.raises(TypeError):
        items[0] = 99  # type: ignore[index]


def test_mutating_original_input_after_event_creation_does_not_mutate_event() -> None:
    """Verify mutating original dictionary/list references after event creation has zero effect."""
    inner_dict = {"sub_key": "original_sub"}
    items_list = [10, 20, 30]
    orig_payload: dict[str, Any] = {
        "top_key": "original_top",
        "nested": inner_dict,
        "list": items_list,
    }

    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload=orig_payload,
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="a" * 64,
        schema_version=1,
    )

    # External mutations on the input structures
    orig_payload["top_key"] = "mutated_top"
    orig_payload["new_external_key"] = "leak"
    inner_dict["sub_key"] = "mutated_sub"
    items_list.append(40)

    # Stored event payload remains completely unchanged
    assert event.payload["top_key"] == "original_top"
    assert "new_external_key" not in event.payload
    assert event.payload["nested"]["sub_key"] == "original_sub"
    assert event.payload["list"] == (10, 20, 30)


def test_event_hash_unchanged_after_attempted_mutations() -> None:
    """Verify that attempted mutations do not alter event_hash or payload integrity."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"a": {"b": 1}},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="e" * 64,
        schema_version=1,
    )

    original_hash = event.event_hash

    with contextlib.suppress(TypeError):
        event.payload["a"]["b"] = 999

    assert event.event_hash == original_hash
    assert event.payload["a"]["b"] == 1


def test_serialization_deserialization_round_trip_deep_immutability() -> None:
    """Verify JSON round-trip deserialization preserves FrozenDict and deep immutability."""
    event = AuditEvent(
        event_id="EVT-0123456789ABCDEF01234567",
        sequence_number=1,
        event_timestamp=datetime.now(UTC),
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"level1": {"level2": "val"}, "nums": [1, 2]},
        previous_event_hash=GENESIS_PREVIOUS_HASH,
        event_hash="d" * 64,
        schema_version=1,
    )

    json_str = event.model_dump_json()
    restored = AuditEvent.model_validate_json(json_str)

    assert isinstance(restored.payload, FrozenDict)
    assert isinstance(restored.payload["level1"], FrozenDict)
    assert isinstance(restored.payload["nums"], tuple)
    assert restored.payload["level1"]["level2"] == "val"

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        restored.payload["level1"]["level2"] = "tampered"
