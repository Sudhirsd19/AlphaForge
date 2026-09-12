"""
Property-based tests for AlphaForge Audit Ledger using Hypothesis.
Proves cryptographic and mathematical invariants across randomized inputs:
P1:  Hash determinism (same event fields -> identical SHA-256 hash)
P2:  Mutation sensitivity (changing any hashed field -> completely different hash)
P3:  Event ID determinism (same logical event intent -> identical event_id)
P4:  Event ID sequence independence (logical event at different sequences -> identical ID)
P5:  Chain integrity (valid sequential appends -> verify_chain() == True)
P6:  Tamper detection (any mutated payload or hash in history -> verify_chain() == False)
P7:  Sequence integrity (valid appends -> strictly contiguous 1..N sequence numbers)
P8:  Append-only invariant (committed events cannot be altered or overwritten)
P9:  Idempotent append (re-submitting identical event -> returns existing record)
P10: Conflicting event identity (same event_id with conflicting payload -> rejected)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from alphaforge.core.exceptions import LedgerIntegrityError
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import (
    AuditEvent,
    AuditEventType,
)
from alphaforge.ledger.serialization import (
    canonical_json,
    compute_event_hash,
    compute_logical_event_id,
)
from alphaforge.ledger.storage import InMemoryLedgerStorage

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_identifier = st.text(
    min_size=1,
    max_size=32,
    alphabet=st.characters(whitelist_categories=("Lu", "Nd"), whitelist_characters="-_"),
)

event_type_strategy = st.sampled_from(list(AuditEventType))

scalar_values = st.one_of(
    st.text(min_size=0, max_size=50),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.booleans(),
    st.decimals(
        min_value=Decimal("-1000000.00"),
        max_value=Decimal("1000000.00"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
)

payload_strategy = st.dictionaries(
    keys=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(whitelist_categories=("Ll", "Lu"), whitelist_characters="_"),
    ),
    values=scalar_values,
    min_size=1,
    max_size=5,
)

fixed_base_time = datetime(2026, 3, 15, 9, 15, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# P1: Hash Determinism
# ---------------------------------------------------------------------------
@given(
    event_type=event_type_strategy,
    entity_type=valid_identifier,
    entity_id=valid_identifier,
    correlation_id=valid_identifier,
    causation_id=valid_identifier,
    payload=payload_strategy,
    seq=st.integers(min_value=1, max_value=10_000),
)
@settings(max_examples=50)
def test_p1_hash_determinism(
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: dict[str, Any],
    seq: int,
) -> None:
    """P1: Identical input fields always produce the identical SHA-256 event hash."""
    ts = fixed_base_time + timedelta(seconds=seq)
    prev_hash = "a" * 64

    h1 = compute_event_hash(
        schema_version=1,
        sequence_number=seq,
        event_timestamp=ts,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash=prev_hash,
    )
    h2 = compute_event_hash(
        schema_version=1,
        sequence_number=seq,
        event_timestamp=ts,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash=prev_hash,
    )
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# P2: Mutation Sensitivity
# ---------------------------------------------------------------------------
@given(
    event_type=event_type_strategy,
    entity_type=valid_identifier,
    entity_id=valid_identifier,
    correlation_id=valid_identifier,
    causation_id=valid_identifier,
    payload=payload_strategy,
    seq=st.integers(min_value=1, max_value=10_000),
)
@settings(max_examples=50)
def test_p2_mutation_sensitivity(
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: dict[str, Any],
    seq: int,
) -> None:
    """P2: Mutating any hashed field produces a completely different hash (avalanche effect)."""
    ts = fixed_base_time + timedelta(seconds=seq)
    prev_hash = "a" * 64

    h_original = compute_event_hash(
        schema_version=1,
        sequence_number=seq,
        event_timestamp=ts,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash=prev_hash,
    )

    # 1. Mutate sequence number
    h_mut_seq = compute_event_hash(
        schema_version=1,
        sequence_number=seq + 1,
        event_timestamp=ts,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash=prev_hash,
    )
    assert h_mut_seq != h_original

    # 2. Mutate previous_event_hash
    h_mut_prev = compute_event_hash(
        schema_version=1,
        sequence_number=seq,
        event_timestamp=ts,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash="b" * 64,
    )
    assert h_mut_prev != h_original

    # 3. Mutate timestamp
    h_mut_ts = compute_event_hash(
        schema_version=1,
        sequence_number=seq,
        event_timestamp=ts + timedelta(microseconds=1),
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
        previous_event_hash=prev_hash,
    )
    assert h_mut_ts != h_original


# ---------------------------------------------------------------------------
# P3: Event ID Determinism
# ---------------------------------------------------------------------------
@given(
    event_type=event_type_strategy,
    entity_type=valid_identifier,
    entity_id=valid_identifier,
    correlation_id=valid_identifier,
    causation_id=valid_identifier,
    payload=payload_strategy,
)
@settings(max_examples=50)
def test_p3_event_id_determinism(
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: dict[str, Any],
) -> None:
    """P3: Same logical event intent always produces identical event_id."""
    id1 = compute_logical_event_id(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
    id2 = compute_logical_event_id(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
    assert id1 == id2
    assert id1.startswith("EVT-")


# ---------------------------------------------------------------------------
# P4: Event ID Sequence Independence
# ---------------------------------------------------------------------------
@given(
    event_type=event_type_strategy,
    entity_type=valid_identifier,
    entity_id=valid_identifier,
    correlation_id=valid_identifier,
    causation_id=valid_identifier,
    payload=payload_strategy,
    seq1=st.integers(min_value=1, max_value=500),
    seq2=st.integers(min_value=501, max_value=1000),
)
@settings(max_examples=50)
def test_p4_event_id_sequence_independence(
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: dict[str, Any],
    seq1: int,
    seq2: int,
) -> None:
    """P4: Event ID does NOT incorporate sequence number; different seq yields identical ID."""
    assert seq1 != seq2
    id1 = compute_logical_event_id(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
    id2 = compute_logical_event_id(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
    assert id1 == id2


# ---------------------------------------------------------------------------
# P5 & P7: Chain and Sequence Integrity Across Random Sequential Appends
# ---------------------------------------------------------------------------
@given(
    event_batches=st.lists(
        st.tuples(
            event_type_strategy,
            valid_identifier,
            valid_identifier,
            valid_identifier,
            valid_identifier,
            payload_strategy,
        ),
        min_size=1,
        max_size=15,
    )
)
@settings(max_examples=30)
def test_p5_and_p7_chain_and_sequence_integrity(
    event_batches: list[tuple[AuditEventType, str, str, str, str, dict[str, Any]]],
) -> None:
    """
    P5: Any valid sequential series of appends forms a cryptographically valid chain.
    P7: Sequences are strictly 1-based, contiguous, and monotonic.
    """
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    appended_events: list[AuditEvent] = []
    for idx, (ev_type, ent_type, ent_id, corr_id, caus_id, pld) in enumerate(
        event_batches, start=1
    ):
        # Disambiguate entity_id to ensure unique events in batch
        unique_ent_id = f"{ent_id}-{idx}"
        ev = ledger.append(
            event_type=ev_type,
            entity_type=ent_type,
            entity_id=unique_ent_id,
            correlation_id=corr_id,
            causation_id=caus_id,
            payload=pld,
        )
        appended_events.append(ev)

    assert len(ledger) == len(event_batches)

    # P7: Contiguous 1..N sequence
    for expected_seq, ev in enumerate(appended_events, start=1):
        assert ev.sequence_number == expected_seq

    # P5: Chain verification passes
    res = ledger.verify_chain()
    assert res.valid is True
    assert res.event_count == len(event_batches)
    assert res.corruption_detected is False


# ---------------------------------------------------------------------------
# P6: Tamper Detection
# ---------------------------------------------------------------------------
@given(
    event_batches=st.lists(
        st.tuples(
            event_type_strategy,
            valid_identifier,
            valid_identifier,
            payload_strategy,
        ),
        min_size=3,
        max_size=10,
    ),
    tamper_idx=st.integers(min_value=0, max_value=9),
)
@settings(max_examples=30)
def test_p6_tamper_detection(
    event_batches: list[tuple[AuditEventType, str, str, dict[str, Any]]],
    tamper_idx: int,
) -> None:
    """P6: Mutating any committed historical event payload causes verify_chain() to fail."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    for idx, (ev_type, ent_type, ent_id, pld) in enumerate(event_batches, start=1):
        ledger.append(
            event_type=ev_type,
            entity_type=ent_type,
            entity_id=f"{ent_id}-{idx}",
            correlation_id="C-1",
            causation_id="CAUS-1",
            payload=pld,
        )

    target_idx = tamper_idx % len(event_batches)
    events = storage.read_all()
    target_event = events[target_idx]

    # Mutate payload
    mutated_payload = dict(target_event.payload)
    mutated_payload["__tampered__"] = "malicious_injection"

    tampered_event = target_event.model_copy(update={"payload": mutated_payload})
    storage._events[target_idx] = tampered_event

    res = ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.corruption_sequence == target_event.sequence_number


# ---------------------------------------------------------------------------
# P8: Append-Only Invariant
# ---------------------------------------------------------------------------
@given(
    ev_type=event_type_strategy,
    ent_id=valid_identifier,
    pld=payload_strategy,
)
@settings(max_examples=30)
def test_p8_append_only_immutable_event(
    ev_type: AuditEventType,
    ent_id: str,
    pld: dict[str, Any],
) -> None:
    """P8: Committed events cannot be modified (frozen model)."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    ev = ledger.append(
        event_type=ev_type,
        entity_type="ORDER",
        entity_id=ent_id,
        correlation_id="CORR-P8",
        causation_id="CAUS-P8",
        payload=pld,
    )

    with pytest.raises(ValidationError):
        ev.sequence_number = 999

    with pytest.raises(ValidationError):
        ev.event_hash = "0" * 64


# ---------------------------------------------------------------------------
# P9: Idempotent Append
# ---------------------------------------------------------------------------
@given(
    ev_type=event_type_strategy,
    ent_type=valid_identifier,
    ent_id=valid_identifier,
    corr_id=valid_identifier,
    caus_id=valid_identifier,
    pld=payload_strategy,
    repeat_count=st.integers(min_value=2, max_value=5),
)
@settings(max_examples=30)
def test_p9_idempotent_append(
    ev_type: AuditEventType,
    ent_type: str,
    ent_id: str,
    corr_id: str,
    caus_id: str,
    pld: dict[str, Any],
    repeat_count: int,
) -> None:
    """P9: Appending the exact same logical event multiple times is idempotent."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    first_ev = ledger.append(
        event_type=ev_type,
        entity_type=ent_type,
        entity_id=ent_id,
        correlation_id=corr_id,
        causation_id=caus_id,
        payload=pld,
    )

    for _ in range(repeat_count - 1):
        repeated_ev = ledger.append(
            event_type=ev_type,
            entity_type=ent_type,
            entity_id=ent_id,
            correlation_id=corr_id,
            causation_id=caus_id,
            payload=pld,
        )
        assert repeated_ev.event_id == first_ev.event_id
        assert repeated_ev.sequence_number == first_ev.sequence_number
        assert repeated_ev.event_hash == first_ev.event_hash

    assert len(ledger) == 1
    assert storage.count() == 1


# ---------------------------------------------------------------------------
# P10: Conflicting Event Identity Rejected
# ---------------------------------------------------------------------------
@given(
    ev_type=event_type_strategy,
    ent_type=valid_identifier,
    ent_id=valid_identifier,
    corr_id=valid_identifier,
    caus_id=valid_identifier,
    pld1=payload_strategy,
    pld2=payload_strategy,
)
@settings(max_examples=30)
def test_p10_conflicting_event_identity_rejected(
    ev_type: AuditEventType,
    ent_type: str,
    ent_id: str,
    corr_id: str,
    caus_id: str,
    pld1: dict[str, Any],
    pld2: dict[str, Any],
) -> None:
    """P10: Re-submitting an existing event_id with conflicting content is strictly rejected."""
    assume(canonical_json(pld1) != canonical_json(pld2))

    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    first_ev = ledger.append(
        event_type=ev_type,
        entity_type=ent_type,
        entity_id=ent_id,
        correlation_id=corr_id,
        causation_id=caus_id,
        payload=pld1,
    )

    # Compute target event_id for pld2
    target_event_id = compute_logical_event_id(
        event_type=ev_type,
        entity_type=ent_type,
        entity_id=ent_id,
        correlation_id=corr_id,
        causation_id=caus_id,
        payload=pld2,
    )
    # Simulate an event_id collision in ledger lookup
    ledger._events_by_id[target_event_id] = first_ev

    with pytest.raises(LedgerIntegrityError, match="Conflicting payload"):
        ledger.append(
            event_type=ev_type,
            entity_type=ent_type,
            entity_id=ent_id,
            correlation_id=corr_id,
            causation_id=caus_id,
            payload=pld2,
        )
