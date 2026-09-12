"""
AlphaForge Ledger Storage Implementations.
Defines the AbstractLedgerStorage interface, InMemoryLedgerStorage for fast simulation,
and FileLedgerStorage for crash-safe, append-only disk durability with fsync guarantees.
"""

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

from alphaforge.core.exceptions import (
    LedgerCorruptionError,
    LedgerStorageError,
)
from alphaforge.ledger.models import AuditEvent


class AbstractLedgerStorage(ABC):
    """
    Abstract interface for append-only audit event persistence.
    """

    @abstractmethod
    def append(self, event: AuditEvent) -> None:
        """Atomically and durably append an immutable audit event."""
        ...

    @abstractmethod
    def read_all(self) -> list[AuditEvent]:
        """Read all persisted audit events in strict sequence order."""
        ...

    @abstractmethod
    def get_last_event(self) -> AuditEvent | None:
        """Return the most recently persisted audit event, or None if empty."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Return the total number of persisted audit events."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Reset storage (testing only)."""
        ...


class InMemoryLedgerStorage(AbstractLedgerStorage):
    """
    Thread-safe, in-memory ledger storage for testing and high-speed simulation.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def read_all(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def get_last_event(self) -> AuditEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class FileLedgerStorage(AbstractLedgerStorage):
    """
    Crash-safe, append-only JSON Lines (.jsonl) durable ledger storage.
    Enforces fsync() on every append to guarantee disk durability before returning.
    Fails closed with LedgerCorruptionError upon encountering truncated or invalid records.
    """

    def __init__(self, file_path: Path | str) -> None:
        self._file_path = Path(file_path).resolve()
        self._lock = threading.RLock()
        self._events: list[AuditEvent] = []
        self._initialized = False

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _ensure_loaded(self) -> None:
        """Load and strictly validate all persisted lines from disk."""
        if self._initialized:
            return

        if not self._file_path.exists():
            self._initialized = True
            return

        events: list[AuditEvent] = []
        try:
            with self._file_path.open("r", encoding="utf-8") as f:
                for line_no, raw_line in enumerate(f, start=1):
                    line = raw_line.strip()
                    if not line:
                        msg = f"Corrupted ledger: empty line at {line_no} in '{self._file_path}'"
                        raise LedgerCorruptionError(msg)
                    try:
                        event = AuditEvent.model_validate_json(line)
                        events.append(event)
                    except (ValidationError, json.JSONDecodeError) as e:
                        msg = f"Corrupted record at line {line_no} in '{self._file_path}': {e}"
                        raise LedgerCorruptionError(msg) from e
        except OSError as e:
            raise LedgerStorageError(f"Failed to read ledger file '{self._file_path}': {e}") from e

        self._events = events
        self._initialized = True

    def append(self, event: AuditEvent) -> None:
        """
        Durable append operation:
        1. Acquire lock.
        2. Ensure state is loaded.
        3. Write single-line JSON to disk.
        4. Explicitly flush and fsync file descriptor.
        5. Update memory cache.
        """
        with self._lock:
            self._ensure_loaded()
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

            payload_line = event.model_dump_json() + "\n"
            try:
                with self._file_path.open("a", encoding="utf-8") as f:
                    f.write(payload_line)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                raise LedgerStorageError(
                    f"Failed to persist ledger event to '{self._file_path}': {e}"
                ) from e

            self._events.append(event)

    def read_all(self) -> list[AuditEvent]:
        with self._lock:
            self._ensure_loaded()
            return list(self._events)

    def get_last_event(self) -> AuditEvent | None:
        with self._lock:
            self._ensure_loaded()
            return self._events[-1] if self._events else None

    def count(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return len(self._events)

    def clear(self) -> None:
        with self._lock:
            if self._file_path.exists():
                self._file_path.unlink()
            self._events.clear()
            self._initialized = True
