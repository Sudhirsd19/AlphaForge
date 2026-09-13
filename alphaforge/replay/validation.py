"""
AlphaForge Replay Engine Validation Authority.
Implements fail-closed verification of cryptographic hash chains, sequence continuity,
timestamp monotonicity, causal lineage, FSM transition legality, and canonical hash equivalence.
"""

from datetime import UTC, datetime

from alphaforge.execution.enums import OrderState
from alphaforge.execution.state_machine import ALLOWED_TRANSITIONS, TERMINAL_STATES
from alphaforge.ledger.models import (
    GENESIS_PREVIOUS_HASH,
    AuditEvent,
)
from alphaforge.ledger.serialization import (
    canonical_json,
    compute_event_hash,
    compute_logical_event_id,
)
from alphaforge.replay.models import ReplayMismatch


class AuditIntegrityValidator:
    """
    Validates cryptographic hash chain, sequence numbering, event IDs,
    timestamp monotonicity, and causal lineage across the replayed event stream.
    """

    @staticmethod
    def validate_event_integrity(
        event: AuditEvent,
        expected_sequence: int,
        expected_previous_hash: str,
        previous_timestamp: datetime | None,
        seen_event_ids: dict[str, AuditEvent],
        known_causal_ids: set[str],
    ) -> ReplayMismatch | None:
        """
        Validate an individual audit event against cryptographic and ledger invariants.
        Returns None if valid, or ReplayMismatch on the first detected anomaly.
        """
        # 1. Sequence continuity
        if event.sequence_number != expected_sequence:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint=f"SEQ_{expected_sequence}",
                replay_fingerprint=f"SEQ_{event.sequence_number}",
                mismatch_category="SEQUENCE_DISCONTINUITY",
                diagnostic=(
                    f"Sequence discontinuity: expected {expected_sequence}, "
                    f"got {event.sequence_number}"
                ),
            )

        # 2. Previous hash chaining
        if event.previous_event_hash != expected_previous_hash:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint=expected_previous_hash,
                replay_fingerprint=event.previous_event_hash,
                mismatch_category="HASH_CHAIN_DIVERGENCE",
                diagnostic=(
                    f"Broken hash link at sequence {event.sequence_number}: expected "
                    f"previous_hash '{expected_previous_hash}', got '{event.previous_event_hash}'"
                ),
            )

        # 3. Timestamp monotonicity (no backwards time travel)
        if previous_timestamp is not None and event.event_timestamp < previous_timestamp:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint=previous_timestamp.astimezone(UTC).isoformat(),
                replay_fingerprint=event.event_timestamp.astimezone(UTC).isoformat(),
                mismatch_category="TIMESTAMP_REGRESSION",
                diagnostic=(
                    f"Timestamp regression at sequence {event.sequence_number}: event timestamp "
                    f"{event.event_timestamp.isoformat()} precedes prior timestamp "
                    f"{previous_timestamp.isoformat()}"
                ),
            )

        # 4. Canonical event ID derivation consistency
        computed_event_id = compute_logical_event_id(
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=event.payload,
            schema_version=event.schema_version,
        )
        if event.event_id != computed_event_id:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint=computed_event_id,
                replay_fingerprint=event.event_id,
                mismatch_category="EVENT_ID_CORRUPTION",
                diagnostic=(
                    f"Event ID corrupted at sequence {event.sequence_number}: computed "
                    f"'{computed_event_id}', got '{event.event_id}'"
                ),
            )

        # 5. Canonical event hash calculation
        computed_event_hash = compute_event_hash(
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
        if event.event_hash != computed_event_hash:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint=computed_event_hash,
                replay_fingerprint=event.event_hash,
                mismatch_category="EVENT_HASH_CORRUPTION",
                diagnostic=(
                    f"Event hash corrupted at sequence {event.sequence_number}: computed "
                    f"'{computed_event_hash}', got '{event.event_hash}'"
                ),
            )

        # 6. Duplicate conflicting event IDs
        if event.event_id in seen_event_ids:
            prior = seen_event_ids[event.event_id]
            if canonical_json(prior.payload) != canonical_json(event.payload):
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    source_fingerprint=canonical_json(prior.payload),
                    replay_fingerprint=canonical_json(event.payload),
                    mismatch_category="DUPLICATE_EVENT_CONFLICT",
                    diagnostic=(
                        f"Conflicting duplicate event_id '{event.event_id}' at sequence "
                        f"{event.sequence_number} (first at {prior.sequence_number})"
                    ),
                )

        # 7. Causal lineage verification
        causation = event.causation_id.strip()
        is_root_causation = (
            causation == GENESIS_PREVIOUS_HASH
            or causation.startswith("ORIGIN:")
            or causation.startswith("BAR:")
            or causation == event.correlation_id
            or causation == event.entity_id
        )
        if not is_root_causation and causation not in known_causal_ids:
            return ReplayMismatch(
                divergent_sequence=event.sequence_number,
                event_id=event.event_id,
                event_type=event.event_type.value,
                source_fingerprint="KNOWN_CAUSAL_ID",
                replay_fingerprint=causation,
                mismatch_category="BROKEN_CAUSAL_LINEAGE",
                diagnostic=(
                    f"Broken causal lineage at sequence {event.sequence_number}: causation_id "
                    f"'{causation}' is not a recognized root and was not preceded by any "
                    "committed event or registered entity"
                ),
            )

        return None


class FSMTransitionValidator:
    """
    Validates order state machine transitions during replay using
    Phase 7 authoritative ALLOWED_TRANSITIONS and TERMINAL_STATES.
    """

    @staticmethod
    def validate_transition(
        order_id: str,
        current_state: OrderState,
        target_state: OrderState,
        sequence_number: int,
        event_id: str,
    ) -> ReplayMismatch | None:
        """
        Verify that transitioning order_id from current_state to target_state is formally legal.
        Returns None if legal, or ReplayMismatch if illegal or reviving a terminal state.
        """
        # Idempotent no-op transition is legal
        if target_state == current_state:
            return None

        # Terminal state protection
        if current_state in TERMINAL_STATES:
            return ReplayMismatch(
                divergent_sequence=sequence_number,
                event_id=event_id,
                event_type="ORDER_TRANSITION",
                source_fingerprint="NON_TERMINAL_MUTATION",
                replay_fingerprint=f"{current_state.value}->{target_state.value}",
                mismatch_category="FSM_TERMINAL_RESURRECTION",
                diagnostic=(
                    f"Order '{order_id}' in terminal state '{current_state.value}' cannot "
                    f"transition to '{target_state.value}'. Terminal states are immutable."
                ),
            )

        allowed = ALLOWED_TRANSITIONS.get(current_state, frozenset())
        if target_state not in allowed:
            allowed_names = sorted(s.value for s in allowed)
            return ReplayMismatch(
                divergent_sequence=sequence_number,
                event_id=event_id,
                event_type="ORDER_TRANSITION",
                source_fingerprint=f"ALLOWED_{allowed_names}",
                replay_fingerprint=target_state.value,
                mismatch_category="FSM_ILLEGAL_TRANSITION",
                diagnostic=(
                    f"Illegal state transition for order '{order_id}': cannot transition from "
                    f"'{current_state.value}' to '{target_state.value}'. Allowed: {allowed_names}"
                ),
            )

        return None


class TraceHashValidator:
    """Validates reconstructed execution trace hash against source trace hash."""

    @staticmethod
    def validate(
        source_trace_hash: str,
        replay_trace_hash: str,
        sequence_number: int,
        event_id: str = "FINAL_TRACE",
    ) -> ReplayMismatch | None:
        if source_trace_hash.strip().lower() != replay_trace_hash.strip().lower():
            return ReplayMismatch(
                divergent_sequence=sequence_number,
                event_id=event_id,
                event_type="TRACE_HASH_CHECK",
                source_fingerprint=source_trace_hash.strip().lower(),
                replay_fingerprint=replay_trace_hash.strip().lower(),
                mismatch_category="TRACE_HASH_MISMATCH",
                diagnostic=(
                    f"Execution trace hash mismatch: source hash '{source_trace_hash}' "
                    f"does not match replayed trace hash '{replay_trace_hash}'"
                ),
            )
        return None


class ResultHashValidator:
    """Validates reconstructed backtest result hash against source result hash."""

    @staticmethod
    def validate(
        source_result_hash: str,
        replay_result_hash: str,
        sequence_number: int,
        event_id: str = "FINAL_RESULT",
    ) -> ReplayMismatch | None:
        if source_result_hash.strip().lower() != replay_result_hash.strip().lower():
            return ReplayMismatch(
                divergent_sequence=sequence_number,
                event_id=event_id,
                event_type="RESULT_HASH_CHECK",
                source_fingerprint=source_result_hash.strip().lower(),
                replay_fingerprint=replay_result_hash.strip().lower(),
                mismatch_category="RESULT_HASH_MISMATCH",
                diagnostic=(
                    f"Backtest result hash mismatch: source hash '{source_result_hash}' "
                    f"does not match replayed result hash '{replay_result_hash}'"
                ),
            )
        return None
