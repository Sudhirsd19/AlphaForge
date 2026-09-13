"""
AlphaForge Fault Injectors.
Deterministic wrappers around production storage, broker, and state store
that inject controlled failures at configured trigger points.
"""

import threading
from typing import Any, cast

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
)
from alphaforge.core.exceptions import (
    BrokerUnavailableError,
    CorruptedStateError,
    LedgerStorageError,
)
from alphaforge.fault_injection.models import ProcessCrashError
from alphaforge.ledger.models import AuditEvent
from alphaforge.ledger.storage import AbstractLedgerStorage
from alphaforge.reconciliation.state_store import RecoverySnapshot


class FailingLedgerStorage(AbstractLedgerStorage):
    """
    Wraps a real LedgerStorage and injects write/read failures
    at a configured trigger count.
    """

    def __init__(
        self,
        delegate: AbstractLedgerStorage,
        fail_on_append_at: int | None = None,
        fail_on_read: bool = False,
        partial_write_at: int | None = None,
    ) -> None:
        self._delegate = delegate
        self._fail_on_append_at = fail_on_append_at
        self._fail_on_read = fail_on_read
        self._partial_write_at = partial_write_at
        self._append_count = 0
        self._lock = threading.RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._append_count += 1
            if (
                self._fail_on_append_at is not None
                and self._append_count == self._fail_on_append_at
            ):
                raise LedgerStorageError(f"Injected write failure at append #{self._append_count}")
            if self._partial_write_at is not None and self._append_count == self._partial_write_at:
                # Simulate partial write: persist to delegate but then raise
                self._delegate.append(event)
                raise LedgerStorageError(f"Injected partial write at append #{self._append_count}")
            self._delegate.append(event)

    def read_all(self) -> list[AuditEvent]:
        if self._fail_on_read:
            raise LedgerStorageError("Injected read failure")
        return self._delegate.read_all()

    def get_last_event(self) -> AuditEvent | None:
        if self._fail_on_read:
            raise LedgerStorageError("Injected read failure")
        return self._delegate.get_last_event()

    def count(self) -> int:
        return self._delegate.count()

    def clear(self) -> None:
        self._delegate.clear()
        with self._lock:
            self._append_count = 0


class CorruptingLedgerStorage(AbstractLedgerStorage):
    """
    Wraps a real LedgerStorage and corrupts specific event fields
    at a configured trigger count.
    """

    def __init__(
        self,
        delegate: AbstractLedgerStorage,
        corrupt_hash_at: int | None = None,
        corrupt_sequence_at: int | None = None,
        drop_event_at: int | None = None,
        duplicate_event_at: int | None = None,
    ) -> None:
        self._delegate = delegate
        self._corrupt_hash_at = corrupt_hash_at
        self._corrupt_sequence_at = corrupt_sequence_at
        self._drop_event_at = drop_event_at
        self._duplicate_event_at = duplicate_event_at
        self._append_count = 0
        self._lock = threading.RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._append_count += 1
            if self._drop_event_at is not None and self._append_count == self._drop_event_at:
                return  # Silently drop
            if (
                self._duplicate_event_at is not None
                and self._append_count == self._duplicate_event_at
            ):
                self._delegate.append(event)
                self._delegate.append(event)
                return
            self._delegate.append(event)

    def read_all(self) -> list[AuditEvent]:
        return self._delegate.read_all()

    def get_last_event(self) -> AuditEvent | None:
        return self._delegate.get_last_event()

    def count(self) -> int:
        return self._delegate.count()

    def clear(self) -> None:
        self._delegate.clear()
        with self._lock:
            self._append_count = 0


class FailingStateStore:
    """
    Wraps AtomicStateStore or InMemoryStateStore and injects
    save/load failures.
    """

    def __init__(
        self,
        delegate: Any,
        fail_on_save: bool = False,
        fail_on_load: bool = False,
        corrupt_on_load: bool = False,
    ) -> None:
        self._delegate = delegate
        self._fail_on_save = fail_on_save
        self._fail_on_load = fail_on_load
        self._corrupt_on_load = corrupt_on_load

    def save_snapshot(self, snapshot: RecoverySnapshot) -> None:
        if self._fail_on_save:
            raise OSError("Injected save failure")
        self._delegate.save_snapshot(snapshot)

    def load_snapshot(self) -> RecoverySnapshot | None:
        if self._fail_on_load:
            raise OSError("Injected load failure")
        if self._corrupt_on_load:
            raise CorruptedStateError("Injected corrupt snapshot")
        return cast("RecoverySnapshot | None", self._delegate.load_snapshot())

    def delete(self) -> None:
        self._delegate.delete()


class TimeoutBroker(AbstractBroker):
    """
    Wraps a real broker and injects timeouts/errors at configured points.
    Deterministic: failures occur at specific operation counts.
    """

    def __init__(
        self,
        delegate: AbstractBroker,
        timeout_on_submit_at: int | None = None,
        error_on_submit_at: int | None = None,
        timeout_on_cancel: bool = False,
        unavailable_on_query: bool = False,
    ) -> None:
        self._delegate = delegate
        self._timeout_on_submit_at = timeout_on_submit_at
        self._error_on_submit_at = error_on_submit_at
        self._timeout_on_cancel = timeout_on_cancel
        self._unavailable_on_query = unavailable_on_query
        self._submit_count = 0
        self._lock = threading.RLock()

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        with self._lock:
            self._submit_count += 1
            if (
                self._timeout_on_submit_at is not None
                and self._submit_count == self._timeout_on_submit_at
            ):
                # Broker accepts internally but we simulate timeout
                self._delegate.submit_order(request)
                raise BrokerUnavailableError(f"Injected timeout at submit #{self._submit_count}")
            if (
                self._error_on_submit_at is not None
                and self._submit_count == self._error_on_submit_at
            ):
                raise BrokerUnavailableError(
                    f"Injected network error at submit #{self._submit_count}"
                )
            return self._delegate.submit_order(request)

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        if self._unavailable_on_query:
            raise BrokerUnavailableError("Injected query unavailability")
        return self._delegate.get_order(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
        )

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        if self._unavailable_on_query:
            raise BrokerUnavailableError("Injected query unavailability")
        return self._delegate.get_open_orders()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        if self._unavailable_on_query:
            raise BrokerUnavailableError("Injected query unavailability")
        return self._delegate.get_positions()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        if self._timeout_on_cancel:
            raise BrokerUnavailableError("Injected cancel timeout")
        return self._delegate.cancel_order(client_order_id)

    def is_available(self) -> bool:
        return self._delegate.is_available()


class CrashSimulator:
    """
    Deterministic crash simulator that raises ProcessCrashError
    at a configured injection point after N operations.
    """

    def __init__(
        self,
        crash_at_operation: int = 1,
        fault_point: str = "UNKNOWN",
    ) -> None:
        self._crash_at = crash_at_operation
        self._fault_point = fault_point
        self._operation_count = 0
        self._lock = threading.RLock()

    def check_and_crash(self, operation_name: str = "") -> None:
        """Call before/after a critical operation to simulate crash."""
        with self._lock:
            self._operation_count += 1
            if self._operation_count == self._crash_at:
                raise ProcessCrashError(
                    self._fault_point,
                    f"Crash at operation #{self._operation_count}: {operation_name}",
                )

    @property
    def operation_count(self) -> int:
        with self._lock:
            return self._operation_count

    def reset(self) -> None:
        with self._lock:
            self._operation_count = 0
