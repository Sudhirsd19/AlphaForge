"""
AlphaForge Audit Ledger Authority.
Implements the deterministic, append-only, tamper-evident cryptographic hash chain.
Provides thread-safe sequence allocation, event-level idempotency, and fail-closed verification.
"""

import threading
from datetime import UTC, datetime
from typing import Any

from alphaforge.core.exceptions import (
    LedgerCorruptionError,
    LedgerIntegrityError,
)
from alphaforge.ledger.models import (
    GENESIS_PREVIOUS_HASH,
    AuditEvent,
    AuditEventType,
    LedgerVerificationResult,
)
from alphaforge.ledger.serialization import (
    canonical_json,
    compute_event_hash,
    compute_logical_event_id,
)
from alphaforge.ledger.storage import AbstractLedgerStorage


class AuditLedger:
    """
    Authoritative, immutable, append-only Audit Ledger for AlphaForge.
    Enforces cryptographic hash chaining, strict monotonic sequence numbering,
    deterministic event identity, and startup tamper verification.
    """

    def __init__(
        self,
        storage: AbstractLedgerStorage,
        auto_verify_on_startup: bool = True,
    ) -> None:
        self._storage = storage
        self._lock = threading.RLock()

        self._events_by_id: dict[str, AuditEvent] = {}
        self._events_by_seq: dict[int, AuditEvent] = {}
        self._events_by_entity: dict[tuple[str, str], list[AuditEvent]] = {}
        self._events_by_correlation: dict[str, list[AuditEvent]] = {}

        self._last_sequence: int = 0
        self._last_event_hash: str | None = None

        if auto_verify_on_startup:
            verification = self.verify_chain()
            if not verification.valid:
                msg = (
                    f"Ledger failed startup verification: {verification.error_message} "
                    f"(sequence {verification.corruption_sequence}, "
                    f"error code {verification.error_code})"
                )
                raise LedgerCorruptionError(msg)
            self._rebuild_indices_from_storage()

    def _rebuild_indices_from_storage(self) -> None:
        """Populate memory lookup indices from verified storage."""
        events = self._storage.read_all()
        self._events_by_id.clear()
        self._events_by_seq.clear()
        self._events_by_entity.clear()
        self._events_by_correlation.clear()

        for ev in events:
            self._events_by_id[ev.event_id] = ev
            self._events_by_seq[ev.sequence_number] = ev
            ent_key = (ev.entity_type.strip().upper(), ev.entity_id.strip().upper())
            self._events_by_entity.setdefault(ent_key, []).append(ev)
            self._events_by_correlation.setdefault(ev.correlation_id.strip(), []).append(ev)

        if events:
            self._last_sequence = events[-1].sequence_number
            self._last_event_hash = events[-1].event_hash
        else:
            self._last_sequence = 0
            self._last_event_hash = None

    def append(
        self,
        event_type: AuditEventType | str,
        entity_type: str,
        entity_id: str,
        correlation_id: str,
        causation_id: str,
        payload: dict[str, Any],
        event_timestamp: datetime | None = None,
        schema_version: int = 1,
    ) -> AuditEvent:
        """
        Atomically append an immutable audit event to the ledger hash chain.

        Guarantees:
        - Deterministic logical event_id generation.
        - Event-level idempotency: if the exact same logical event is submitted again,
          returns the existing event with no new sequence number and no duplicate entry.
        - Cryptographic hash chaining: previous_event_hash -> event_hash.
        - Strict 1-based monotonic sequence numbering.
        """
        with self._lock:
            now = event_timestamp if event_timestamp is not None else datetime.now(UTC)
            if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
                raise LedgerIntegrityError(f"Timestamp must be timezone-aware UTC: {now}")

            clean_event_type = (
                event_type
                if isinstance(event_type, AuditEventType)
                else AuditEventType(str(event_type).strip())
            )
            clean_entity_type = entity_type.strip().upper()
            clean_entity_id = entity_id.strip().upper()
            clean_correlation_id = correlation_id.strip()
            clean_causation_id = causation_id.strip()

            # 1. Derive deterministic logical event identity (independent of sequence number)
            event_id = compute_logical_event_id(
                event_type=clean_event_type,
                entity_type=clean_entity_type,
                entity_id=clean_entity_id,
                correlation_id=clean_correlation_id,
                causation_id=clean_causation_id,
                payload=payload,
                schema_version=schema_version,
            )

            # 2. Event-level idempotency check
            if event_id in self._events_by_id:
                existing = self._events_by_id[event_id]
                # Compare canonical payload content
                if canonical_json(existing.payload) != canonical_json(payload):
                    raise LedgerIntegrityError(
                        f"Conflicting payload for already committed event_id '{event_id}'. "
                        "Conflicting duplicates are strictly rejected."
                    )
                return existing

            # 3. Allocate next sequence number
            next_seq = self._last_sequence + 1

            # 4. Resolve previous event hash
            prev_hash = (
                self._last_event_hash
                if (self._last_sequence > 0 and self._last_event_hash is not None)
                else GENESIS_PREVIOUS_HASH
            )

            # 5. Compute authoritative SHA-256 event hash
            ev_hash = compute_event_hash(
                schema_version=schema_version,
                sequence_number=next_seq,
                event_timestamp=now,
                event_type=clean_event_type,
                entity_type=clean_entity_type,
                entity_id=clean_entity_id,
                correlation_id=clean_correlation_id,
                causation_id=clean_causation_id,
                payload=payload,
                previous_event_hash=prev_hash,
            )

            # 6. Construct immutable AuditEvent
            event = AuditEvent(
                event_id=event_id,
                sequence_number=next_seq,
                event_timestamp=now,
                event_type=clean_event_type,
                entity_type=clean_entity_type,
                entity_id=clean_entity_id,
                correlation_id=clean_correlation_id,
                causation_id=clean_causation_id,
                payload=payload,
                previous_event_hash=prev_hash,
                event_hash=ev_hash,
                schema_version=schema_version,
            )

            # 7. Persist to storage
            self._storage.append(event)

            # 8. Update in-memory state and indices
            self._last_sequence = next_seq
            self._last_event_hash = ev_hash
            self._events_by_id[event_id] = event
            self._events_by_seq[next_seq] = event
            ent_key = (clean_entity_type, clean_entity_id)
            self._events_by_entity.setdefault(ent_key, []).append(event)
            self._events_by_correlation.setdefault(clean_correlation_id, []).append(event)

            return event

    def verify_chain(self) -> LedgerVerificationResult:
        """
        Verify the entire ledger hash chain from genesis through the latest event.
        Inspects sequence continuity, previous hash linkage, canonical hash accuracy,
        and event identity consistency.
        Fails closed on any detected anomaly.
        """
        with self._lock:
            events = self._storage.read_all()
            if not events:
                return LedgerVerificationResult(
                    valid=True,
                    event_count=0,
                )

            seen_event_ids: dict[str, AuditEvent] = {}

            for i, event in enumerate(events):
                expected_seq = i + 1

                # 1. Verify sequence continuity
                if event.sequence_number != expected_seq:
                    return LedgerVerificationResult(
                        valid=False,
                        event_count=len(events),
                        first_sequence=events[0].sequence_number,
                        last_sequence=events[-1].sequence_number,
                        corruption_detected=True,
                        corruption_sequence=event.sequence_number,
                        corruption_event_id=event.event_id,
                        error_code="SEQUENCE_DISCONTINUITY",
                        error_message=(
                            f"Sequence discontinuity at index {i}: expected {expected_seq}, "
                            f"got {event.sequence_number}"
                        ),
                        verified_through_sequence=i if i > 0 else None,
                    )

                # 2. Verify previous_event_hash linkage
                expected_prev = GENESIS_PREVIOUS_HASH if i == 0 else events[i - 1].event_hash
                if event.previous_event_hash != expected_prev:
                    return LedgerVerificationResult(
                        valid=False,
                        event_count=len(events),
                        first_sequence=events[0].sequence_number,
                        last_sequence=events[-1].sequence_number,
                        corruption_detected=True,
                        corruption_sequence=event.sequence_number,
                        corruption_event_id=event.event_id,
                        error_code="HASH_LINK_CORRUPTED",
                        error_message=(
                            f"Broken hash link at sequence {event.sequence_number}: expected "
                            f"previous_hash '{expected_prev}', got '{event.previous_event_hash}'"
                        ),
                        verified_through_sequence=i if i > 0 else None,
                    )

                # 3. Verify event_id derivation consistency
                expected_event_id = compute_logical_event_id(
                    event_type=event.event_type,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    payload=event.payload,
                    schema_version=event.schema_version,
                )
                if event.event_id != expected_event_id:
                    return LedgerVerificationResult(
                        valid=False,
                        event_count=len(events),
                        first_sequence=events[0].sequence_number,
                        last_sequence=events[-1].sequence_number,
                        corruption_detected=True,
                        corruption_sequence=event.sequence_number,
                        corruption_event_id=event.event_id,
                        error_code="EVENT_ID_CORRUPTED",
                        error_message=(
                            f"Event ID corrupted at sequence {event.sequence_number}: expected "
                            f"'{expected_event_id}', got '{event.event_id}'"
                        ),
                        verified_through_sequence=i if i > 0 else None,
                    )

                # 4. Verify canonical event_hash calculation
                computed_hash = compute_event_hash(
                    schema_version=event.schema_version,
                    sequence_number=event.sequence_number,
                    event_timestamp=event.event_timestamp,
                    event_type=event.event_type,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    payload=event.payload,
                    previous_event_hash=event.previous_event_hash,
                )
                if event.event_hash != computed_hash:
                    return LedgerVerificationResult(
                        valid=False,
                        event_count=len(events),
                        first_sequence=events[0].sequence_number,
                        last_sequence=events[-1].sequence_number,
                        corruption_detected=True,
                        corruption_sequence=event.sequence_number,
                        corruption_event_id=event.event_id,
                        error_code="EVENT_HASH_CORRUPTED",
                        error_message=(
                            f"Event hash corrupted at sequence {event.sequence_number}: expected "
                            f"'{computed_hash}', got '{event.event_hash}'"
                        ),
                        verified_through_sequence=i if i > 0 else None,
                    )

                # 5. Verify no duplicate conflicting event IDs
                if event.event_id in seen_event_ids:
                    prev_ev = seen_event_ids[event.event_id]
                    if canonical_json(prev_ev.payload) != canonical_json(event.payload):
                        return LedgerVerificationResult(
                            valid=False,
                            event_count=len(events),
                            first_sequence=events[0].sequence_number,
                            last_sequence=events[-1].sequence_number,
                            corruption_detected=True,
                            corruption_sequence=event.sequence_number,
                            corruption_event_id=event.event_id,
                            error_code="DUPLICATE_EVENT_ID_CONFLICT",
                            error_message=(
                                f"Duplicate conflicting event_id '{event.event_id}' at sequence "
                                f"{event.sequence_number} (first at {prev_ev.sequence_number})"
                            ),
                            verified_through_sequence=i if i > 0 else None,
                        )
                seen_event_ids[event.event_id] = event

            return LedgerVerificationResult(
                valid=True,
                event_count=len(events),
                first_sequence=events[0].sequence_number,
                last_sequence=events[-1].sequence_number,
                corruption_detected=False,
                verified_through_sequence=events[-1].sequence_number,
            )

    def get_event(self, event_id: str) -> AuditEvent | None:
        """Query an audit event by logical event_id."""
        with self._lock:
            return self._events_by_id.get(event_id.strip())

    def get_event_by_sequence(self, sequence_number: int) -> AuditEvent | None:
        """Query an audit event by its sequence_number."""
        with self._lock:
            return self._events_by_seq.get(sequence_number)

    def get_events_by_entity(self, entity_type: str, entity_id: str) -> tuple[AuditEvent, ...]:
        """Query all events associated with a specific entity."""
        with self._lock:
            key = (entity_type.strip().upper(), entity_id.strip().upper())
            return tuple(self._events_by_entity.get(key, ()))

    def get_events_by_correlation_id(self, correlation_id: str) -> tuple[AuditEvent, ...]:
        """Query all events sharing a common correlation_id in sequence order."""
        with self._lock:
            return tuple(self._events_by_correlation.get(correlation_id.strip(), ()))

    def get_last_event(self) -> AuditEvent | None:
        """Return the most recently committed audit event, or None if ledger is empty."""
        with self._lock:
            return self._storage.get_last_event()

    def __len__(self) -> int:
        with self._lock:
            return self._last_sequence
