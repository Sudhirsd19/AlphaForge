"""
Phase 12 — Storage Failure Tests (S1–S5).
Proves AlphaForge fails closed on storage write/read failures,
truncated JSONL, and corrupted state snapshots.
"""

from pathlib import Path

import pytest

from alphaforge.core.exceptions import (
    CorruptedStateError,
    LedgerCorruptionError,
    LedgerStorageError,
)
from alphaforge.fault_injection.injectors import FailingLedgerStorage
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import (
    FileLedgerStorage,
    InMemoryLedgerStorage,
)
from alphaforge.reconciliation.state_store import AtomicStateStore


class TestS1WriteFailure:
    """S1: Write failure during ledger append raises LedgerStorageError."""

    def test_failing_storage_raises_on_append(self) -> None:
        delegate = InMemoryLedgerStorage()
        failing = FailingLedgerStorage(delegate, fail_on_append_at=1)
        ledger = AuditLedger(failing, auto_verify_on_startup=False)

        with pytest.raises(LedgerStorageError, match="Injected write"):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id="SIG-S1",
                correlation_id="CORR-S1",
                causation_id="ROOT",
                payload={"symbol": "NIFTY"},
            )

    def test_write_failure_on_second_event(self) -> None:
        delegate = InMemoryLedgerStorage()
        failing = FailingLedgerStorage(delegate, fail_on_append_at=2)
        ledger = AuditLedger(failing, auto_verify_on_startup=False)

        # First event succeeds
        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-S1A",
            correlation_id="CORR-S1",
            causation_id="ROOT",
            payload={"idx": 1},
        )
        assert ev1 is not None

        # Second event fails
        with pytest.raises(LedgerStorageError, match="Injected write"):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id="SIG-S1B",
                correlation_id="CORR-S1",
                causation_id=ev1.event_id,
                payload={"idx": 2},
            )


class TestS2PartialWrite:
    """S2: Partial write — event saved but error raised."""

    def test_partial_write_persists_event_but_raises(self) -> None:
        delegate = InMemoryLedgerStorage()
        failing = FailingLedgerStorage(delegate, partial_write_at=1)
        ledger = AuditLedger(failing, auto_verify_on_startup=False)

        with pytest.raises(LedgerStorageError, match="partial write"):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id="SIG-S2",
                correlation_id="CORR-S2",
                causation_id="ROOT",
                payload={"symbol": "NIFTY"},
            )
        # Event was actually persisted in delegate
        assert delegate.count() == 1


class TestS3ReadFailure:
    """S3: Read failure blocks all ledger operations."""

    def test_failing_read_raises_storage_error(self) -> None:
        delegate = InMemoryLedgerStorage()
        failing = FailingLedgerStorage(delegate, fail_on_read=True)
        with pytest.raises(LedgerStorageError, match="Injected read"):
            failing.read_all()

    def test_failing_get_last_event_raises(self) -> None:
        delegate = InMemoryLedgerStorage()
        failing = FailingLedgerStorage(delegate, fail_on_read=True)
        with pytest.raises(LedgerStorageError, match="Injected read"):
            failing.get_last_event()


class TestS4TruncatedJSONL:
    """S4: Truncated JSONL ledger file causes fail-closed corruption error."""

    def test_truncated_jsonl_detected(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "ledger_truncated.jsonl"
        # Write a valid event then truncate it
        storage = FileLedgerStorage(ledger_file)
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-S4",
            correlation_id="CORR-S4",
            causation_id="ROOT",
            payload={"symbol": "NIFTY"},
        )
        # Now corrupt the file by truncating
        content = ledger_file.read_text(encoding="utf-8")
        ledger_file.write_text(content[: len(content) // 2], encoding="utf-8")
        # Re-open: should detect corruption
        with pytest.raises(LedgerCorruptionError):
            corrupt_storage = FileLedgerStorage(ledger_file)
            AuditLedger(corrupt_storage, auto_verify_on_startup=True)

    def test_empty_line_in_jsonl_detected(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "ledger_empty_line.jsonl"
        # Write valid content then add empty line
        storage = FileLedgerStorage(ledger_file)
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-S4B",
            correlation_id="CORR-S4B",
            causation_id="ROOT",
            payload={"symbol": "NIFTY"},
        )
        # Inject empty line
        with ledger_file.open("a", encoding="utf-8") as f:
            f.write("\n")
        # Re-open: empty line is corruption
        with pytest.raises(LedgerCorruptionError, match="empty line"):
            corrupt_storage = FileLedgerStorage(ledger_file)
            AuditLedger(corrupt_storage, auto_verify_on_startup=True)


class TestS5CorruptedState:
    """S5: Corrupted recovery state snapshot fails closed."""

    def test_invalid_json_raises_corrupted_state(self, tmp_path: Path) -> None:
        state_file = tmp_path / "corrupt_state.json"
        state_file.write_text("{invalid json", encoding="utf-8")
        store = AtomicStateStore(state_file)
        with pytest.raises(CorruptedStateError):
            store.load_snapshot()

    def test_empty_file_raises_corrupted_state(self, tmp_path: Path) -> None:
        state_file = tmp_path / "empty_state.json"
        state_file.write_text("", encoding="utf-8")
        store = AtomicStateStore(state_file)
        with pytest.raises(CorruptedStateError, match="empty"):
            store.load_snapshot()

    def test_schema_invalid_snapshot_raises(self, tmp_path: Path) -> None:
        state_file = tmp_path / "bad_schema_state.json"
        state_file.write_text(
            '{"schema_version": 1, "extra_field": true}',
            encoding="utf-8",
        )
        store = AtomicStateStore(state_file)
        with pytest.raises(CorruptedStateError):
            store.load_snapshot()
