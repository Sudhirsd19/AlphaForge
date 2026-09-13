"""
AlphaForge Observability Sinks and Safe Dispatcher.

Provides sink abstractions, an in-memory bounded ring buffer, a local JSONL file sink,
and the fail-safe isolation dispatcher ensuring observability failures never corrupt
or interrupt core trading execution.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphaforge.observability.events import ObservabilityEvent


class AbstractObservabilitySink(ABC):
    """Abstract protocol for observability event destinations."""

    @abstractmethod
    def emit(self, event: ObservabilityEvent) -> None:
        """Deliver an event to the sink."""

    @abstractmethod
    def flush(self) -> None:
        """Flush any pending buffered events."""

    @abstractmethod
    def close(self) -> None:
        """Release underlying resources and finalize sink."""


class InMemoryObservabilitySink(AbstractObservabilitySink):
    """
    Thread-safe, bounded in-memory ring buffer sink.

    Invariants:
    - Fixed capacity (default 1,000 events).
    - When capacity is reached, the oldest event is deterministically evicted.
    - dropped_count increments monotonically with every dropped event.
    - External reads return immutable copies (cannot mutate internal storage).
    """

    def __init__(self, capacity: int = 1000) -> None:
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._buffer: deque[ObservabilityEvent] = deque(maxlen=capacity)
        self._total_received: int = 0
        self._dropped_count: int = 0
        self._lock = threading.RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

    @property
    def total_received(self) -> int:
        with self._lock:
            return self._total_received

    @property
    def events(self) -> tuple[ObservabilityEvent, ...]:
        """Return an immutable snapshot of buffered events in FIFO order."""
        with self._lock:
            return tuple(self._buffer)

    def emit(self, event: ObservabilityEvent) -> None:
        with self._lock:
            self._total_received += 1
            if len(self._buffer) == self._capacity:
                self._dropped_count += 1
            self._buffer.append(event)

    def flush(self) -> None:
        # In-memory buffer is always immediately flushed
        pass

    def close(self) -> None:
        pass

    def clear(self) -> None:
        """Clear all buffered events and reset counters."""
        with self._lock:
            self._buffer.clear()
            self._total_received = 0
            self._dropped_count = 0

    def get_by_correlation_id(self, correlation_id: str) -> list[ObservabilityEvent]:
        """Filter events matching the given correlation_id."""
        with self._lock:
            return [e for e in self._buffer if e.correlation_id == correlation_id]

    def get_by_type(self, event_type: str) -> list[ObservabilityEvent]:
        """Filter events matching the given event_type."""
        with self._lock:
            return [e for e in self._buffer if e.event_type == event_type]


class JsonlObservabilitySink(AbstractObservabilitySink):
    """
    Append-only local JSONL file sink.

    Invariants:
    - Encoded as UTF-8, exactly one valid JSON object per line.
    - Thread-safe append operations.
    - Safe flush and resource cleanup.
    """

    def __init__(self, file_path: str | Path, max_bytes: int | None = None) -> None:
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._lock = threading.RLock()
        self._file = self._path.open("a", encoding="utf-8")
        self._closed = False

    @property
    def file_path(self) -> Path:
        return self._path

    def emit(self, event: ObservabilityEvent) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot emit to closed JsonlObservabilitySink")

            json_line = event.to_json() + "\n"
            self._file.write(json_line)

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True


class SafeObservabilityDispatcher:
    """
    Fail-safe dispatcher protecting trading workflows from diagnostic failures.

    Invariants:
    - Sink failures (disk full, network outage, permission errors) are isolated.
    - Serialization and event formatting failures are isolated.
    - Failures are NEVER swallowed silently: internal diagnostic counters track
      sink_failure_count, dispatcher_error_count, dropped_event_count, and emitted_event_count.
    - Core execution logic is guaranteed to proceed regardless of sink state.
    """

    def __init__(self, sinks: list[AbstractObservabilitySink] | None = None) -> None:
        self._sinks: list[AbstractObservabilitySink] = list(sinks or [])
        self._lock = threading.RLock()
        self._emitted_event_count: int = 0
        self._dropped_event_count: int = 0
        self._sink_failure_count: int = 0
        self._dispatcher_error_count: int = 0
        self._last_error: Exception | None = None

    @property
    def sinks(self) -> tuple[AbstractObservabilitySink, ...]:
        with self._lock:
            return tuple(self._sinks)

    @property
    def emitted_event_count(self) -> int:
        with self._lock:
            return self._emitted_event_count

    @property
    def dropped_event_count(self) -> int:
        with self._lock:
            return self._dropped_event_count

    @property
    def sink_failure_count(self) -> int:
        with self._lock:
            return self._sink_failure_count

    @property
    def dispatcher_error_count(self) -> int:
        with self._lock:
            return self._dispatcher_error_count

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def add_sink(self, sink: AbstractObservabilitySink) -> None:
        """Register a new sink with the dispatcher."""
        with self._lock:
            if sink not in self._sinks:
                self._sinks.append(sink)

    def remove_sink(self, sink: AbstractObservabilitySink) -> None:
        """Unregister a sink from the dispatcher."""
        with self._lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def emit(self, event: ObservabilityEvent) -> None:
        """
        Dispatch an event to all registered sinks with fail-safe isolation.
        Any sink exception is caught, recorded in diagnostic counters, and prevented
        from bubbling up to caller trading logic.
        """
        with self._lock:
            if not self._sinks:
                self._dropped_event_count += 1
                return

            self._emitted_event_count += 1
            for sink in self._sinks:
                try:
                    sink.emit(event)
                except Exception as ex:
                    self._sink_failure_count += 1
                    self._last_error = ex

    def emit_safely(self, event_factory: Any) -> None:
        """
        Safely instantiate and emit an event.
        Isolates any exceptions during event construction or serialization.
        """
        try:
            event = event_factory() if callable(event_factory) else event_factory
            self.emit(event)
        except Exception as ex:
            with self._lock:
                self._dispatcher_error_count += 1
                self._last_error = ex

    def flush(self) -> None:
        """Flush all registered sinks safely."""
        with self._lock:
            for sink in self._sinks:
                try:
                    sink.flush()
                except Exception as ex:
                    self._sink_failure_count += 1
                    self._last_error = ex

    def close(self) -> None:
        """Close all registered sinks safely."""
        with self._lock:
            for sink in self._sinks:
                try:
                    sink.close()
                except Exception as ex:
                    self._sink_failure_count += 1
                    self._last_error = ex
