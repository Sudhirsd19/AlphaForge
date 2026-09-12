"""
Unit tests for AlphaForge InMemoryLedgerStorage and FileLedgerStorage.
Verifies append-only durability, reload, fsync execution, and fail-closed corruption detection.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from alphaforge.core.exceptions import LedgerCorruptionError
from alphaforge.ledger.models import AuditEvent, AuditEventType
from alphaforge.ledger.storage import FileLedgerStorage, InMemoryLedgerStorage


def _create_sample_event(seq: int, prev_hash: str = "GENESIS") -> AuditEvent:
    now = datetime(2026, 9, 12, 12, 0, seq, tzinfo=UTC)
    return AuditEvent(
        event_id=f"EVT-{seq:024d}",
        sequence_number=seq,
        event_timestamp=now,
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id=f"ORD-{seq}",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"index": seq},
        previous_event_hash=prev_hash,
        event_hash=f"{seq:02x}" * 32,
        schema_version=1,
    )


def test_in_memory_storage_basic_lifecycle() -> None:
    """Verify in-memory storage append, read_all, get_last_event, and count."""
    storage = InMemoryLedgerStorage()
    assert storage.count() == 0
    assert storage.get_last_event() is None
    assert storage.read_all() == []

    ev1 = _create_sample_event(1)
    ev2 = _create_sample_event(2, prev_hash=ev1.event_hash)

    storage.append(ev1)
    storage.append(ev2)

    assert storage.count() == 2
    assert storage.get_last_event() == ev2
    assert storage.read_all() == [ev1, ev2]

    storage.clear()
    assert storage.count() == 0


def test_file_storage_durability_and_reload(tmp_path: Path) -> None:
    """Verify file storage persists events to disk and reloads accurately on restart."""
    ledger_path = tmp_path / "ledger.jsonl"
    storage1 = FileLedgerStorage(ledger_path)

    ev1 = _create_sample_event(1)
    ev2 = _create_sample_event(2, prev_hash=ev1.event_hash)

    storage1.append(ev1)
    storage1.append(ev2)
    assert storage1.count() == 2

    # Simulate restart with fresh storage instance reading the same file
    storage2 = FileLedgerStorage(ledger_path)
    assert storage2.count() == 2
    assert storage2.get_last_event() == ev2
    assert storage2.read_all() == [ev1, ev2]


def test_file_storage_executes_fsync(tmp_path: Path) -> None:
    """Verify each append explicitly flushes and fsyncs the underlying file descriptor."""
    ledger_path = tmp_path / "ledger.jsonl"
    storage = FileLedgerStorage(ledger_path)
    ev1 = _create_sample_event(1)

    with patch("os.fsync") as mock_fsync:
        storage.append(ev1)
        assert mock_fsync.call_count == 1


def test_file_storage_fails_closed_on_corrupted_json(tmp_path: Path) -> None:
    """Verify malformed JSON line in storage raises LedgerCorruptionError."""
    ledger_path = tmp_path / "corrupt_ledger.jsonl"
    ev1 = _create_sample_event(1)
    storage = FileLedgerStorage(ledger_path)
    storage.append(ev1)

    # Append corrupted JSON to file directly
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write('{"corrupted": true, broken json\n')

    # Reloading storage must fail closed
    corrupt_storage = FileLedgerStorage(ledger_path)
    with pytest.raises(LedgerCorruptionError, match="Corrupted record at line 2"):
        corrupt_storage.read_all()


def test_file_storage_fails_closed_on_blank_line(tmp_path: Path) -> None:
    """Verify blank line in middle of ledger raises LedgerCorruptionError."""
    ledger_path = tmp_path / "blank_line_ledger.jsonl"
    ev1 = _create_sample_event(1)
    storage = FileLedgerStorage(ledger_path)
    storage.append(ev1)

    # Append blank line
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write("\n")

    corrupt_storage = FileLedgerStorage(ledger_path)
    with pytest.raises(LedgerCorruptionError, match="empty line"):
        corrupt_storage.read_all()
