"""
Unit tests for Phase 14 Observability Sinks and Resilience.

Covers:
- OBS18: Sink failure isolation (broken sink cannot disrupt caller).
- OBS19: Bounded in-memory ring buffer overflow and dropped_count tracking.
- OBS20: Safe shutdown flush behavior.
- ADV-OBS-1: Serialization exception isolation.
- ADV-OBS-2: Sink unavailable isolation (disk/network failure).
- ADV-OBS-3: Secret injection into payload scrubbed across sinks.
- ADV-OBS-10: Queue reaches capacity with deterministic overflow behavior.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from alphaforge.observability.events import (
    ObservabilityCategory,
    ObservabilityEvent,
    OrderEventType,
)
from alphaforge.observability.sinks import (
    AbstractObservabilitySink,
    InMemoryObservabilitySink,
    JsonlObservabilitySink,
    SafeObservabilityDispatcher,
)
from alphaforge.security.redaction import REDACTION_MASK


class BrokenSink(AbstractObservabilitySink):
    """Test double sink that intentionally raises runtime errors on emit/flush/close."""

    def emit(self, event: ObservabilityEvent) -> None:  # noqa: ARG002
        raise OSError("Disk quota exceeded: simulated sink failure")

    def flush(self) -> None:
        raise OSError("Flush error: simulated I/O failure")

    def close(self) -> None:
        raise OSError("Close error: simulated resource lock")


def test_obs18_and_adv_obs_2_sink_failure_isolation() -> None:
    """OBS18 & ADV-OBS-2: Broken sink does not raise or interrupt execution."""
    broken = BrokenSink()
    working = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([broken, working])

    event = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
    )

    # Must NOT raise exception despite broken sink
    dispatcher.emit(event)

    # Diagnostic counts must reflect the failure
    assert dispatcher.sink_failure_count == 1
    assert dispatcher.emitted_event_count == 1
    assert dispatcher.last_error is not None
    assert "Disk quota exceeded" in str(dispatcher.last_error)

    # Working sink still received the event
    assert len(working.events) == 1
    assert working.events[0].event_type == "ORDER_SUBMIT"

    # Flush and close must also isolate failures
    dispatcher.flush()
    dispatcher.close()


def test_obs19_and_adv_obs_10_bounded_buffer_overflow() -> None:
    """OBS19 & ADV-OBS-10: Ring buffer fixed capacity deterministically evicts oldest event."""
    capacity = 5
    sink = InMemoryObservabilitySink(capacity=capacity)
    dispatcher = SafeObservabilityDispatcher([sink])

    for i in range(12):
        ev = ObservabilityEvent(
            event_type=OrderEventType.ORDER_SUBMIT.value,
            category=ObservabilityCategory.ORDER,
            symbol="NIFTY",
            attributes={"seq": i},
        )
        dispatcher.emit(ev)

    assert sink.total_received == 12
    assert len(sink.events) == capacity
    assert sink.dropped_count == 7  # 12 - 5 = 7 dropped

    # Buffer contains the last 5 events (seq 7, 8, 9, 10, 11)
    seqs = [e.attributes["seq"] for e in sink.events]
    assert seqs == [7, 8, 9, 10, 11]


def test_obs20_shutdown_flush_behavior(tmp_path: Path) -> None:
    """OBS20: Verify that JsonlObservabilitySink flushes and closes cleanly upon shutdown."""
    log_file = tmp_path / "obs_test.jsonl"
    jsonl_sink = JsonlObservabilitySink(log_file)
    dispatcher = SafeObservabilityDispatcher([jsonl_sink])

    for i in range(3):
        dispatcher.emit(
            ObservabilityEvent(
                event_type="TEST_SHUTDOWN",
                category=ObservabilityCategory.SYSTEM,
                message=f"Event {i}",
            )
        )

    dispatcher.flush()
    dispatcher.close()

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        data = json.loads(line)
        assert data["event_type"] == "TEST_SHUTDOWN"


def test_adv_obs_1_serialization_exception_isolation() -> None:
    """ADV-OBS-1: Serialization or event construction errors are isolated."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    def failing_factory() -> Any:
        raise ValueError("Simulated serialization crash")

    # emit_safely catches factory / serialization exceptions
    dispatcher.emit_safely(failing_factory)

    assert dispatcher.dispatcher_error_count == 1
    assert dispatcher.last_error is not None
    assert "Simulated serialization crash" in str(dispatcher.last_error)
    assert len(sink.events) == 0


def test_adv_obs_3_secret_injection_payload_scrubbed(tmp_path: Path) -> None:
    """ADV-OBS-3: Raw secret injected into payload is scrubbed in-memory and on-disk."""
    log_file = tmp_path / "secrets_test.jsonl"
    jsonl_sink = JsonlObservabilitySink(log_file)
    mem_sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([jsonl_sink, mem_sink])

    secret_raw = "password=SuperSecretLivePass99!"  # noqa: S105
    ev = ObservabilityEvent(
        event_type="AUTH_TEST",
        category=ObservabilityCategory.SECURITY,
        message=f"Auth attempt with {secret_raw}",
        attributes={"injected_secret": secret_raw},
    )
    dispatcher.emit(ev)
    dispatcher.flush()
    dispatcher.close()

    # Verify in-memory sink
    assert len(mem_sink.events) == 1
    assert "SuperSecretLivePass99!" not in mem_sink.events[0].message
    assert REDACTION_MASK in mem_sink.events[0].message
    assert "SuperSecretLivePass99!" not in str(mem_sink.events[0].attributes)

    # Verify JSONL file on disk
    content = log_file.read_text(encoding="utf-8")
    assert "SuperSecretLivePass99!" not in content
    assert REDACTION_MASK in content
