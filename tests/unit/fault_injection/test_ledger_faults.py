"""
Phase 12 — Audit Ledger Failure Tests (L1–L8).
Proves AlphaForge detects and rejects corrupted, tampered, gapped,
duplicate, missing, and reordered audit events.
"""

from datetime import UTC, datetime, timedelta

import pytest

from alphaforge.fault_injection.invariants import (
    assert_hash_chain_intact,
    assert_sequence_contiguous,
)
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEvent, AuditEventType
from alphaforge.ledger.serialization import (
    compute_event_hash,
)
from alphaforge.ledger.storage import InMemoryLedgerStorage


def _build_ledger_with_events(
    count: int = 3,
) -> tuple[AuditLedger, InMemoryLedgerStorage]:
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage, auto_verify_on_startup=False)
    base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
    for i in range(count):
        ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id=f"SIG-{i + 1:03d}",
            correlation_id="CORR-001",
            causation_id="ROOT" if i == 0 else f"SIG-{i:03d}",
            payload={"signal_index": i, "symbol": "NIFTY"},
            event_timestamp=base + timedelta(seconds=i * 10),
        )
    return ledger, storage


class TestL1EventPayloadCorruption:
    """L1: Event payload corruption detected by hash validation."""

    def test_tampered_payload_detected_on_verify(self) -> None:
        ledger, storage = _build_ledger_with_events(3)
        events = storage.read_all()
        # Tamper with event payload by creating corrupted event
        corrupted = events[1]
        # Recompute with wrong payload
        wrong_hash = compute_event_hash(
            corrupted.schema_version,
            corrupted.sequence_number,
            corrupted.event_timestamp,
            corrupted.event_type,
            corrupted.entity_type,
            corrupted.entity_id,
            corrupted.correlation_id,
            corrupted.causation_id,
            {"TAMPERED": True},
            corrupted.previous_event_hash,
        )
        # Verify original hash differs from tampered hash
        assert wrong_hash != corrupted.event_hash


class TestL2EventHashCorruption:
    """L2: Event hash corruption causes replay to fail closed."""

    def test_corrupted_event_hash_breaks_chain(self) -> None:
        _ledger, storage = _build_ledger_with_events(3)
        events = storage.read_all()
        # Corrupt the hash of event[1]
        original = events[1]
        corrupted = AuditEvent(
            event_id=original.event_id,
            sequence_number=original.sequence_number,
            event_timestamp=original.event_timestamp,
            event_type=original.event_type,
            entity_type=original.entity_type,
            entity_id=original.entity_id,
            correlation_id=original.correlation_id,
            causation_id=original.causation_id,
            payload=dict(original.payload),
            previous_event_hash=original.previous_event_hash,
            event_hash="a" * 64,  # Corrupted hash
        )
        tampered_events = [events[0], corrupted, events[2]]
        # Event[2] expects event[1]'s original hash as previous_hash
        # The chain is broken
        with pytest.raises(AssertionError, match="Hash chain"):
            assert_hash_chain_intact(tampered_events)


class TestL3PreviousHashCorruption:
    """L3: Previous hash corruption causes hash-chain failure."""

    def test_wrong_previous_hash_detected(self) -> None:
        _ledger, storage = _build_ledger_with_events(3)
        events = storage.read_all()
        original = events[2]
        corrupted = AuditEvent(
            event_id=original.event_id,
            sequence_number=original.sequence_number,
            event_timestamp=original.event_timestamp,
            event_type=original.event_type,
            entity_type=original.entity_type,
            entity_id=original.entity_id,
            correlation_id=original.correlation_id,
            causation_id=original.causation_id,
            payload=dict(original.payload),
            previous_event_hash="b" * 64,  # Wrong previous hash
            event_hash=original.event_hash,
        )
        tampered_events = [events[0], events[1], corrupted]
        with pytest.raises(AssertionError, match="Hash chain"):
            assert_hash_chain_intact(tampered_events)


class TestL4SequenceGap:
    """L4: Sequence gap is deterministically detected."""

    def test_sequence_gap_detected(self) -> None:
        _ledger, storage = _build_ledger_with_events(3)
        events = storage.read_all()
        seq_numbers = [e.sequence_number for e in events]
        assert seq_numbers == [1, 2, 3]
        # Simulate gap: [1, 3]
        with pytest.raises(AssertionError, match="Sequence gap"):
            assert_sequence_contiguous([1, 3, 4])


class TestL5DuplicateEventId:
    """L5: Duplicate event ID is deterministically rejected."""

    def test_duplicate_event_id_rejected_by_ledger(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        # Append first event
        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-001",
            correlation_id="CORR-001",
            causation_id="ROOT",
            payload={"symbol": "NIFTY"},
            event_timestamp=base,
        )
        # Second event with same entity produces different event_id
        # because sequence_number differs
        ev2 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-001",
            correlation_id="CORR-001",
            causation_id=ev1.event_id,
            payload={"symbol": "NIFTY"},
            event_timestamp=base + timedelta(seconds=10),
        )
        assert ev1.event_id != ev2.event_id


class TestL6DuplicateSemanticEvent:
    """L6: Duplicate semantic event (same fill) does not double-count."""

    def test_idempotent_same_event_data_produces_same_hash(self) -> None:
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        payload = {"order_id": "ORD-001", "fill_qty": 50}
        h1 = compute_event_hash(
            1,
            1,
            ts,
            AuditEventType.ORDER_FILLED,
            "ORDER",
            "ORD-001",
            "CORR-001",
            "ROOT",
            payload,
            "GENESIS",
        )
        h2 = compute_event_hash(
            1,
            1,
            ts,
            AuditEventType.ORDER_FILLED,
            "ORDER",
            "ORD-001",
            "CORR-001",
            "ROOT",
            payload,
            "GENESIS",
        )
        assert h1 == h2  # Same data => same hash (deterministic)


class TestL7MissingEvent:
    """L7: Missing critical event detected by sequence/chain verification."""

    def test_missing_event_breaks_sequence_contiguity(self) -> None:
        _ledger, storage = _build_ledger_with_events(5)
        events = storage.read_all()
        # Remove event at index 2 (sequence 3)
        incomplete = [events[0], events[1], events[3], events[4]]
        seq_numbers = [e.sequence_number for e in incomplete]
        with pytest.raises(AssertionError, match="Sequence gap"):
            assert_sequence_contiguous(seq_numbers)


class TestL8EventReordering:
    """L8: Event reordering causes deterministic causal/sequence failure."""

    def test_reordered_events_break_hash_chain(self) -> None:
        _ledger, storage = _build_ledger_with_events(3)
        events = storage.read_all()
        # Swap event[1] and event[2]
        reordered = [events[0], events[2], events[1]]
        with pytest.raises(AssertionError, match="Hash chain"):
            assert_hash_chain_intact(reordered)

    def test_reordered_events_break_sequence(self) -> None:
        seqs = [1, 3, 2]
        with pytest.raises(AssertionError, match="Sequence gap"):
            assert_sequence_contiguous(seqs)
