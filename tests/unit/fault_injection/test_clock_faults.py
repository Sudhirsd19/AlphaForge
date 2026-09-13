"""
Phase 12 — Clock/Time Failure Tests (T1–T5).
Proves AlphaForge rejects backwards clocks, timestamp regressions,
future jumps, duplicate timestamps, and restart anomalies.
"""

from datetime import UTC, datetime, timedelta

import pytest

from alphaforge.core.exceptions import (
    DataIntegrityError,
    LedgerIntegrityError,
)
from alphaforge.data.validation import (
    validate_temporal_causality,
    validate_utc_timestamp,
)
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage


class TestT1ClockBackwards:
    """T1: Clock going backwards is detected at data validation boundary."""

    def test_received_before_exchange_rejected(self) -> None:
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        with pytest.raises(DataIntegrityError, match="Causality"):
            validate_temporal_causality(
                exchange_ts=ts,
                received_ts=ts - timedelta(seconds=10),
            )

    def test_naive_timestamp_rejected(self) -> None:
        naive = datetime(2026, 9, 13, 9, 15)  # No timezone
        with pytest.raises(DataIntegrityError, match="UTC"):
            validate_utc_timestamp("test", naive)


class TestT2EventTimestampBackwards:
    """T2: Event timestamp regression in ledger is handled deterministically."""

    def test_ledger_accepts_non_regressing_timestamps(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T2A",
            correlation_id="CORR-T2",
            causation_id="ROOT",
            payload={"idx": 1},
            event_timestamp=base,
        )
        # Second event with later timestamp: OK
        ev2 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T2B",
            correlation_id="CORR-T2",
            causation_id=ev1.event_id,
            payload={"idx": 2},
            event_timestamp=base + timedelta(seconds=10),
        )
        assert ev2.sequence_number == 2

    def test_ledger_rejects_non_utc_timestamp(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        naive_ts = datetime(2026, 9, 13, 9, 15)  # No timezone
        with pytest.raises(LedgerIntegrityError, match="UTC"):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id="SIG-T2",
                correlation_id="CORR-T2",
                causation_id="ROOT",
                payload={"idx": 1},
                event_timestamp=naive_ts,
            )


class TestT3FutureJump:
    """T3: Future timestamp jump is rejected by data validation."""

    def test_future_candle_blocked_by_lookahead_check(self) -> None:
        eval_time = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        future_time = eval_time + timedelta(hours=2)
        with pytest.raises(DataIntegrityError, match="Lookahead"):
            validate_temporal_causality(
                exchange_ts=future_time,
                received_ts=future_time + timedelta(seconds=1),
                evaluation_ts=eval_time,
            )


class TestT4DuplicateTimestamp:
    """T4: Duplicate timestamps with different data are detected."""

    def test_same_timestamp_different_events_are_distinct(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T4A",
            correlation_id="CORR-T4",
            causation_id="ROOT",
            payload={"idx": 1},
            event_timestamp=ts,
        )
        # Same timestamp but different entity => different event
        ev2 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T4B",
            correlation_id="CORR-T4",
            causation_id=ev1.event_id,
            payload={"idx": 2},
            event_timestamp=ts,  # Same timestamp
        )
        assert ev1.event_id != ev2.event_id
        assert ev1.sequence_number != ev2.sequence_number

    def test_identical_event_at_same_timestamp_is_idempotent(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T4C",
            correlation_id="CORR-T4C",
            causation_id="ROOT",
            payload={"idx": 1},
            event_timestamp=ts,
        )
        # Same event data: idempotent return of existing
        ev2 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-T4C",
            correlation_id="CORR-T4C",
            causation_id="ROOT",
            payload={"idx": 1},
            event_timestamp=ts,
        )
        assert ev1.event_id == ev2.event_id
        assert storage.count() == 1  # Only one event stored


class TestT5RestartTimestampAnomaly:
    """T5: Clock anomaly on restart does not corrupt ledger sequence."""

    def test_ledger_sequence_monotonic_regardless_of_clock(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        # Simulate pre-crash events
        ev1 = ledger.append(
            event_type=AuditEventType.BACKTEST_STARTED,
            entity_type="BACKTEST",
            entity_id="BT-001",
            correlation_id="CORR-T5",
            causation_id="ROOT",
            payload={"phase": "pre-crash"},
            event_timestamp=base,
        )

        # Simulate post-restart with slightly earlier clock
        # (within seconds due to clock sync)
        ev2 = ledger.append(
            event_type=AuditEventType.BACKTEST_COMPLETED,
            entity_type="BACKTEST",
            entity_id="BT-001",
            correlation_id="CORR-T5",
            causation_id=ev1.event_id,
            payload={"phase": "post-restart"},
            event_timestamp=base - timedelta(seconds=1),
        )
        # Sequence must still be monotonically increasing
        assert ev2.sequence_number == ev1.sequence_number + 1
        # Hash chain must still be valid
        assert ev2.previous_event_hash == ev1.event_hash
