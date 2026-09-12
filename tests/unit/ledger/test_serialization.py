"""
Unit tests for AlphaForge canonical serialization and cryptographic hashing.
Verifies recursive key sorting, exact Decimal serialization, deterministic event_id derivation,
and SHA-256 event hash sensitivity.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.serialization import (
    canonical_json,
    compute_event_hash,
    compute_logical_event_id,
)


def test_canonical_json_recursive_key_sorting() -> None:
    """Verify dictionary keys are sorted at all nesting levels."""
    d1 = {"z": 1, "a": {"k": 2, "b": 3}, "m": [1, 2]}
    d2 = {"a": {"b": 3, "k": 2}, "m": [1, 2], "z": 1}
    assert canonical_json(d1) == canonical_json(d2)
    assert canonical_json(d1) == '{"a":{"b":3,"k":2},"m":[1,2],"z":1}'


def test_canonical_json_decimal_exact_serialization() -> None:
    """Verify Decimal values serialize to exact fixed-point string without float conversion."""
    d = Decimal("24500.10")
    payload = {"price": d}
    serialized = canonical_json(payload)
    assert serialized == '{"price":"24500.10"}'

    # Test small decimal that would normally produce scientific notation in float/str
    small = Decimal("0.0000001")
    assert canonical_json({"val": small}) == '{"val":"0.0000001"}'


def test_compute_logical_event_id_determinism() -> None:
    """Verify compute_logical_event_id produces identical output for identical logical content."""
    id1 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50, "symbol": "NIFTY"},
    )
    id2 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"symbol": "NIFTY", "quantity": 50},  # Different key order
    )
    assert id1 == id2
    assert id1.startswith("EVT-")
    assert len(id1) == 28  # "EVT-" + 24 hex characters


def test_compute_logical_event_id_sequence_independence() -> None:
    """Verify logical event_id does NOT depend on sequence number or timing."""
    id1 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    # The function compute_logical_event_id does not even accept sequence_number or timestamp!
    id2 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    assert id1 == id2


def test_compute_logical_event_id_distinct_for_different_events() -> None:
    """Verify different entities or payloads yield distinct event_ids."""
    id1 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    id2 = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-2",  # Different ID
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    assert id1 != id2


def test_compute_event_hash_determinism_and_sensitivity() -> None:
    """Verify SHA-256 event_hash calculation determinism and sensitivity to all inputs."""
    now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    h1 = compute_event_hash(
        schema_version=1,
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"quantity": 50},
        previous_event_hash="GENESIS",
    )
    h2 = compute_event_hash(
        schema_version=1,
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"quantity": 50},
        previous_event_hash="GENESIS",
    )
    assert h1 == h2
    assert len(h1) == 64

    # Sensitivity to sequence number (mandatory: sequence IS in event_hash)
    h_seq2 = compute_event_hash(
        schema_version=1,
        sequence_number=2,  # Changed
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"quantity": 50},
        previous_event_hash="GENESIS",
    )
    assert h1 != h_seq2

    # Sensitivity to previous_event_hash
    h_prev_diff = compute_event_hash(
        schema_version=1,
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"quantity": 50},
        previous_event_hash="0" * 64,  # Changed
    )
    assert h1 != h_prev_diff

    # Sensitivity to payload Decimal precision
    h_dec1 = compute_event_hash(
        schema_version=1,
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"price": Decimal("24500.10")},
        previous_event_hash="GENESIS",
    )
    h_dec2 = compute_event_hash(
        schema_version=1,
        sequence_number=1,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="CORR-1",
        causation_id="CAUS-1",
        payload={"price": Decimal("24500.11")},  # Changed
        previous_event_hash="GENESIS",
    )
    assert h_dec1 != h_dec2
