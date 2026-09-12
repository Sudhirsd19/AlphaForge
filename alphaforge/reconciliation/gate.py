"""
AlphaForge Reconciliation Gate Module.
Enforces structural blocking of all new order entries until state reconciliation
is completely verified, matched, and confirmed protected.
"""

import threading
from datetime import UTC, datetime

from alphaforge.core.exceptions import ReconciliationError
from alphaforge.reconciliation.models import ReconciliationResult, ReconciliationStatus


class ReconciliationGate:
    """
    Thread-safe execution gate controlling order entry permissions.
    Starts closed (FAIL-CLOSED) and blocks all new order creation until authoritative
    reconciliation against broker facts succeeds.
    """

    def __init__(self, initially_open: bool = False) -> None:
        self._is_open: bool = initially_open
        self._reason: str = "Initial startup state (reconciliation pending)"
        self._last_opened_at: datetime | None = None
        self._last_closed_at: datetime = datetime.now(UTC)
        self._lock: threading.RLock = threading.RLock()

    @property
    def is_open(self) -> bool:
        """Query whether the gate is open."""
        with self._lock:
            return self._is_open

    @property
    def reason(self) -> str:
        """Query the reason for current gate state."""
        with self._lock:
            return self._reason

    @property
    def last_opened_at(self) -> datetime | None:
        with self._lock:
            return self._last_opened_at

    @property
    def last_closed_at(self) -> datetime:
        with self._lock:
            return self._last_closed_at

    def can_accept_new_entries(self) -> bool:
        """
        Structural execution guard for order dispatchers.
        Returns True ONLY if reconciliation has proven complete alignment.
        """
        with self._lock:
            return self._is_open

    def open(self, result: ReconciliationResult) -> None:
        """
        Open the reconciliation gate.
        Enforces strict preconditions:
        - Result status must be MATCHED.
        - new_entries_allowed must be True.
        - manual_escalation_required must be False.
        - mismatch_count must be 0.
        - unknown_count must be 0.
        """
        with self._lock:
            now = datetime.now(UTC)
            if result.status != ReconciliationStatus.MATCHED:
                self.close(f"Cannot open gate: Reconciliation status is '{result.status}'")
                raise ReconciliationError(
                    f"Cannot open gate: Reconciliation status is '{result.status}'"
                )

            if not result.new_entries_allowed:
                self.close("Cannot open gate: Result has new_entries_allowed=False")
                raise ReconciliationError("Cannot open gate: Result has new_entries_allowed=False")

            if result.manual_escalation_required:
                self.close("Cannot open gate: Result requires manual escalation")
                raise ReconciliationError("Cannot open gate: Result requires manual escalation")

            if result.mismatch_count > 0 or result.unknown_count > 0:
                msg = (
                    f"Cannot open gate: Mismatches={result.mismatch_count}, "
                    f"Unknowns={result.unknown_count}"
                )
                self.close(msg)
                raise ReconciliationError(msg)

            self._is_open = True
            self._reason = f"Reconciliation '{result.reconciliation_id}' verified matched"
            self._last_opened_at = now

    def close(self, reason: str) -> None:
        """
        Immediately lock the gate and block all new entries.
        """
        with self._lock:
            self._is_open = False
            self._reason = reason or "Gate locked"
            self._last_closed_at = datetime.now(UTC)
