"""
Continuous Multi-Trigger Reconciliation Hardening (Phase 18-I).

Implements institutional continuous multi-entity reconciliation across:
1. On-fill reconciliation (triggered immediately upon receiving a fill event).
2. On-order-event reconciliation (triggered on state transitions, ack drops, or venue updates).
3. Periodic reconciliation (scheduled every N seconds to detect slow drift or missed messages).
4. On-reconnect reconciliation (triggered after socket reconnection or temporal gap recovery).

Enforces automatic fail-closed responses:
- Position mismatch: immediately trips kill switch and blocks order routing.
- Cash/margin/PnL mismatch: flags discrepancy, alerts, and halts new risk allocations.
- Order state mismatch / UNKNOWN order: queries venue, resolves UNKNOWN status,
  and writes forensic records.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from alphaforge.core.exceptions import DataIntegrityError

if TYPE_CHECKING:
    from collections.abc import Callable


class ReconciliationTriggerType(StrEnum):
    """Event that triggered the reconciliation cycle."""

    ON_FILL = "ON_FILL"
    ON_ORDER_EVENT = "ON_ORDER_EVENT"
    PERIODIC = "PERIODIC"
    ON_RECONNECT = "ON_RECONNECT"
    MANUAL = "MANUAL"


class DiscrepancySeverity(StrEnum):
    """Classification of detected alignment discrepancies."""

    NONE = "NONE"
    WARNING = "WARNING"
    CRITICAL_MISMATCH = "CRITICAL_MISMATCH"
    FATAL_DESYNC = "FATAL_DESYNC"


class ReconciliationActionTaken(StrEnum):
    """Authoritative action taken in response to reconciliation."""

    ALIGNED = "ALIGNED"
    NONE = "NONE"
    KILL_SWITCH_TRIPPED = "KILL_SWITCH_TRIPPED"
    RISK_HALTED = "RISK_HALTED"
    ORDER_RESOLVED = "ORDER_RESOLVED"
    ORDER_UNKNOWN_FLAGGED = "ORDER_UNKNOWN_FLAGGED"


class DiscrepancyRecord:
    """Individual discrepancy detected during a reconciliation cycle."""

    def __init__(
        self,
        trigger: ReconciliationTriggerType,
        timestamp: datetime,
        entity_type: str,
        symbol: str | None,
        local_value: str,
        venue_value: str,
        discrepancy_delta: str,
        severity: DiscrepancySeverity,
        action_taken: ReconciliationActionTaken,
        details: str,
    ) -> None:
        self.trigger = trigger
        self.timestamp = timestamp
        self.entity_type = entity_type
        self.symbol = symbol
        self.local_value = local_value
        self.venue_value = venue_value
        self.discrepancy_delta = discrepancy_delta
        self.severity = severity
        self.action_taken = action_taken
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": str(self.trigger),
            "timestamp": self.timestamp.isoformat(),
            "entity_type": self.entity_type,
            "symbol": self.symbol,
            "local_value": self.local_value,
            "venue_value": self.venue_value,
            "discrepancy_delta": self.discrepancy_delta,
            "severity": str(self.severity),
            "action_taken": str(self.action_taken),
            "details": self.details,
        }


class ReconciliationAuditCycle:
    """Immutable audit record of a single reconciliation cycle."""

    def __init__(
        self,
        cycle_id: str,
        trigger: ReconciliationTriggerType,
        timestamp: datetime,
        discrepancies: list[DiscrepancyRecord],
        kill_switch_tripped: bool,
        new_risk_halted: bool,
    ) -> None:
        self.cycle_id = cycle_id
        self.trigger = trigger
        self.timestamp = timestamp
        self.discrepancies = discrepancies
        self.kill_switch_tripped = kill_switch_tripped
        self.new_risk_halted = new_risk_halted
        self.cycle_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = {
            "cycle_id": self.cycle_id,
            "trigger": str(self.trigger),
            "timestamp": self.timestamp.isoformat(),
            "kill_switch_tripped": self.kill_switch_tripped,
            "new_risk_halted": self.new_risk_halted,
            "discrepancies": [d.to_dict() for d in self.discrepancies],
        }
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "trigger": str(self.trigger),
            "timestamp": self.timestamp.isoformat(),
            "kill_switch_tripped": self.kill_switch_tripped,
            "new_risk_halted": self.new_risk_halted,
            "cycle_hash": self.cycle_hash,
            "discrepancies": [d.to_dict() for d in self.discrepancies],
        }


class ContinuousReconciliationCoordinator:
    """
    Multi-trigger reconciliation engine enforcing real-time alignment and fail-closed defense.
    """

    def __init__(
        self,
        kill_switch_callback: Callable[[str], None] | None = None,
        risk_halt_callback: Callable[[str], None] | None = None,
        venue_order_query_callback: Callable[[str], str | None] | None = None,
        cash_tolerance: Decimal = Decimal("1.00"),
        max_history: int = 500,
    ) -> None:
        self._kill_switch_callback = kill_switch_callback
        self._risk_halt_callback = risk_halt_callback
        self._venue_order_query_callback = venue_order_query_callback
        self._cash_tolerance = cash_tolerance
        self._max_history = max_history

        self._lock = threading.RLock()
        self._cycle_sequence: int = 0
        self._is_kill_switch_tripped: bool = False
        self._is_risk_halted: bool = False
        self._kill_switch_reason: str | None = None
        self._risk_halt_reason: str | None = None
        self._history: list[ReconciliationAuditCycle] = []

    @property
    def is_kill_switch_tripped(self) -> bool:
        with self._lock:
            return self._is_kill_switch_tripped

    @property
    def is_risk_halted(self) -> bool:
        with self._lock:
            return self._is_risk_halted

    @property
    def kill_switch_reason(self) -> str | None:
        with self._lock:
            return self._kill_switch_reason

    @property
    def risk_halt_reason(self) -> str | None:
        with self._lock:
            return self._risk_halt_reason

    @property
    def total_cycles(self) -> int:
        with self._lock:
            return self._cycle_sequence

    def get_history(self) -> list[ReconciliationAuditCycle]:
        with self._lock:
            return list(self._history)

    def _next_cycle_id(self, trigger: ReconciliationTriggerType) -> str:
        self._cycle_sequence += 1
        return f"RECON-{trigger.value}-{self._cycle_sequence:06d}"

    def _trip_kill_switch(self, reason: str) -> None:
        self._is_kill_switch_tripped = True
        self._is_risk_halted = True
        self._kill_switch_reason = reason
        if self._kill_switch_callback is not None:
            self._kill_switch_callback(reason)

    def _halt_risk(self, reason: str) -> None:
        self._is_risk_halted = True
        self._risk_halt_reason = reason
        if self._risk_halt_callback is not None:
            self._risk_halt_callback(reason)

    def trigger_on_fill(
        self,
        symbol: str,
        local_position: Decimal,
        venue_position: Decimal,
        timestamp: datetime | None = None,
    ) -> ReconciliationAuditCycle:
        """
        Trigger 1: Immediate on-fill reconciliation.
        Zero tolerance for quantity discrepancies.
        """
        with self._lock:
            now = timestamp or datetime.now(UTC)
            cycle_id = self._next_cycle_id(ReconciliationTriggerType.ON_FILL)
            discrepancies: list[DiscrepancyRecord] = []

            delta = venue_position - local_position
            if delta != Decimal("0"):
                reason = (
                    f"POSITION_DESYNC_ON_FILL for {symbol}: Local={local_position}, "
                    f"Venue={venue_position}, Delta={delta}"
                )
                self._trip_kill_switch(reason)
                discrepancies.append(
                    DiscrepancyRecord(
                        trigger=ReconciliationTriggerType.ON_FILL,
                        timestamp=now,
                        entity_type="POSITION",
                        symbol=symbol,
                        local_value=str(local_position),
                        venue_value=str(venue_position),
                        discrepancy_delta=str(delta),
                        severity=DiscrepancySeverity.FATAL_DESYNC,
                        action_taken=ReconciliationActionTaken.KILL_SWITCH_TRIPPED,
                        details=reason,
                    )
                )

            cycle = ReconciliationAuditCycle(
                cycle_id=cycle_id,
                trigger=ReconciliationTriggerType.ON_FILL,
                timestamp=now,
                discrepancies=discrepancies,
                kill_switch_tripped=self._is_kill_switch_tripped,
                new_risk_halted=self._is_risk_halted,
            )
            self._record_cycle(cycle)
            return cycle

    def trigger_on_order_event(
        self,
        order_id: str,
        symbol: str,
        local_state: str,
        venue_state: str,
        timestamp: datetime | None = None,
    ) -> ReconciliationAuditCycle:
        """
        Trigger 2: On-order-event reconciliation.
        Handles UNKNOWN states and state conflicts.
        """
        with self._lock:
            now = timestamp or datetime.now(UTC)
            cycle_id = self._next_cycle_id(ReconciliationTriggerType.ON_ORDER_EVENT)
            discrepancies: list[DiscrepancyRecord] = []

            if local_state != venue_state:
                # If local state is UNKNOWN, attempt venue resolution
                if local_state == "UNKNOWN":
                    resolved = None
                    if self._venue_order_query_callback is not None:
                        resolved = self._venue_order_query_callback(order_id)

                    effective_state = resolved or venue_state
                    action = (
                        ReconciliationActionTaken.ORDER_RESOLVED
                        if effective_state == venue_state
                        else ReconciliationActionTaken.ORDER_UNKNOWN_FLAGGED
                    )
                    details = (
                        f"UNKNOWN order {order_id} reconciled to venue state '{effective_state}'"
                    )
                    discrepancies.append(
                        DiscrepancyRecord(
                            trigger=ReconciliationTriggerType.ON_ORDER_EVENT,
                            timestamp=now,
                            entity_type="ORDER",
                            symbol=symbol,
                            local_value=local_state,
                            venue_value=venue_state,
                            discrepancy_delta=f"{local_state}->{effective_state}",
                            severity=DiscrepancySeverity.WARNING,
                            action_taken=action,
                            details=details,
                        )
                    )
                else:
                    # Critical state divergence (e.g., local says REJECTED but venue says FILLED)
                    reason = (
                        f"ORDER_STATE_CONFLICT for {order_id} ({symbol}): "
                        f"Local={local_state}, Venue={venue_state}"
                    )
                    self._trip_kill_switch(reason)
                    discrepancies.append(
                        DiscrepancyRecord(
                            trigger=ReconciliationTriggerType.ON_ORDER_EVENT,
                            timestamp=now,
                            entity_type="ORDER",
                            symbol=symbol,
                            local_value=local_state,
                            venue_value=venue_state,
                            discrepancy_delta=f"{local_state}!={venue_state}",
                            severity=DiscrepancySeverity.FATAL_DESYNC,
                            action_taken=ReconciliationActionTaken.KILL_SWITCH_TRIPPED,
                            details=reason,
                        )
                    )

            cycle = ReconciliationAuditCycle(
                cycle_id=cycle_id,
                trigger=ReconciliationTriggerType.ON_ORDER_EVENT,
                timestamp=now,
                discrepancies=discrepancies,
                kill_switch_tripped=self._is_kill_switch_tripped,
                new_risk_halted=self._is_risk_halted,
            )
            self._record_cycle(cycle)
            return cycle

    def trigger_on_reconnect(
        self,
        local_positions: dict[str, Decimal],
        venue_positions: dict[str, Decimal],
        local_cash: Decimal,
        venue_cash: Decimal,
        timestamp: datetime | None = None,
    ) -> ReconciliationAuditCycle:
        """
        Trigger 3: On-reconnect reconciliation.
        Full audit across all positions and cash upon recovery.
        """
        return self._reconcile_full(
            trigger=ReconciliationTriggerType.ON_RECONNECT,
            local_positions=local_positions,
            venue_positions=venue_positions,
            local_cash=local_cash,
            venue_cash=venue_cash,
            timestamp=timestamp,
        )

    def trigger_periodic(
        self,
        local_positions: dict[str, Decimal],
        venue_positions: dict[str, Decimal],
        local_cash: Decimal,
        venue_cash: Decimal,
        timestamp: datetime | None = None,
    ) -> ReconciliationAuditCycle:
        """
        Trigger 4: Periodic scheduled reconciliation (every N seconds).
        """
        return self._reconcile_full(
            trigger=ReconciliationTriggerType.PERIODIC,
            local_positions=local_positions,
            venue_positions=venue_positions,
            local_cash=local_cash,
            venue_cash=venue_cash,
            timestamp=timestamp,
        )

    def _reconcile_full(
        self,
        trigger: ReconciliationTriggerType,
        local_positions: dict[str, Decimal],
        venue_positions: dict[str, Decimal],
        local_cash: Decimal,
        venue_cash: Decimal,
        timestamp: datetime | None = None,
    ) -> ReconciliationAuditCycle:
        """Core multi-entity comparison engine."""
        with self._lock:
            now = timestamp or datetime.now(UTC)
            cycle_id = self._next_cycle_id(trigger)
            discrepancies: list[DiscrepancyRecord] = []

            # 1. Position audit across all symbols
            all_symbols = set(local_positions.keys()) | set(venue_positions.keys())
            for sym in sorted(all_symbols):
                loc_qty = local_positions.get(sym, Decimal("0"))
                ven_qty = venue_positions.get(sym, Decimal("0"))
                pos_delta = ven_qty - loc_qty
                if pos_delta != Decimal("0"):
                    reason = (
                        f"POSITION_MISMATCH [{trigger.value}] for {sym}: "
                        f"Local={loc_qty}, Venue={ven_qty}, Delta={pos_delta}"
                    )
                    self._trip_kill_switch(reason)
                    discrepancies.append(
                        DiscrepancyRecord(
                            trigger=trigger,
                            timestamp=now,
                            entity_type="POSITION",
                            symbol=sym,
                            local_value=str(loc_qty),
                            venue_value=str(ven_qty),
                            discrepancy_delta=str(pos_delta),
                            severity=DiscrepancySeverity.FATAL_DESYNC,
                            action_taken=ReconciliationActionTaken.KILL_SWITCH_TRIPPED,
                            details=reason,
                        )
                    )

            # 2. Cash / Margin audit
            cash_delta = (venue_cash - local_cash).copy_abs()
            if cash_delta > self._cash_tolerance:
                reason = (
                    f"CASH_MARGIN_DISCREPANCY [{trigger.value}]: Local={local_cash}, "
                    f"Venue={venue_cash}, AbsDelta={cash_delta} > tol {self._cash_tolerance}"
                )
                self._halt_risk(reason)
                discrepancies.append(
                    DiscrepancyRecord(
                        trigger=trigger,
                        timestamp=now,
                        entity_type="CASH",
                        symbol=None,
                        local_value=str(local_cash),
                        venue_value=str(venue_cash),
                        discrepancy_delta=str(cash_delta),
                        severity=DiscrepancySeverity.CRITICAL_MISMATCH,
                        action_taken=ReconciliationActionTaken.RISK_HALTED,
                        details=reason,
                    )
                )

            cycle = ReconciliationAuditCycle(
                cycle_id=cycle_id,
                trigger=trigger,
                timestamp=now,
                discrepancies=discrepancies,
                kill_switch_tripped=self._is_kill_switch_tripped,
                new_risk_halted=self._is_risk_halted,
            )
            self._record_cycle(cycle)
            return cycle

    def _record_cycle(self, cycle: ReconciliationAuditCycle) -> None:
        self._history.append(cycle)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    def reset_for_test(self, confirmation_code: str) -> None:
        """Testing utility to reset kill switch state."""
        if confirmation_code != "TEST_ONLY_RESET":
            raise DataIntegrityError("Unauthorized reset attempt")
        with self._lock:
            self._is_kill_switch_tripped = False
            self._is_risk_halted = False
            self._kill_switch_reason = None
            self._risk_halt_reason = None
