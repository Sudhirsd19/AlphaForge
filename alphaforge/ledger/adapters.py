"""
AlphaForge Audit Ledger Adapters.
Provides non-invasive observers for Phase 7 Order FSM and Phase 8 Reconciliation.
Guarantees audit failure isolation: audit recording failures are surfaced to the application
without corrupting or mutating operational state machine transitions or reconciliation gates.
"""

import threading
from typing import Any

from alphaforge.execution.enums import OrderState
from alphaforge.execution.models import TransitionEvent
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEvent, AuditEventType
from alphaforge.reconciliation.models import (
    ReconciliationResult,
    ReconciliationStatus,
)

_STATE_TO_EVENT_TYPE: dict[OrderState, AuditEventType] = {
    OrderState.CREATED: AuditEventType.ORDER_CREATED,
    OrderState.VALIDATED: AuditEventType.ORDER_VALIDATED,
    OrderState.SUBMITTED: AuditEventType.ORDER_SUBMITTED,
    OrderState.ACKNOWLEDGED: AuditEventType.ORDER_ACKNOWLEDGED,
    OrderState.PARTIALLY_FILLED: AuditEventType.ORDER_PARTIAL_FILL,
    OrderState.FILLED: AuditEventType.ORDER_FILLED,
    OrderState.PROTECTION_PENDING: AuditEventType.PROTECTION_PENDING,
    OrderState.PROTECTED: AuditEventType.PROTECTION_CONFIRMED,
    OrderState.CANCELLED: AuditEventType.ORDER_CANCELLED,
    OrderState.REJECTED: AuditEventType.ORDER_REJECTED,
    OrderState.UNKNOWN: AuditEventType.ORDER_UNKNOWN,
    OrderState.MANUAL_ESCALATION: AuditEventType.MANUAL_ESCALATION,
}


class OrderFSMTransitionAuditor:
    """
    Non-invasive observer that records Phase 7 OrderStateMachine transitions in the AuditLedger.
    Does not modify FSM state or legality.
    Maintains deterministic previous-event causal lineage per logical order stream.
    """

    def __init__(self, ledger: AuditLedger) -> None:
        self._ledger = ledger
        self._last_audit_error: Exception | None = None
        self._lock = threading.RLock()
        self._last_event_by_order: dict[str, str] = {}

    @property
    def last_audit_error(self) -> Exception | None:
        return self._last_audit_error

    def register_order_causation(self, order_id: str, causation_id: str) -> None:
        """Explicitly register upstream causal predecessor for an order's initial transition."""
        clean_id = order_id.strip().upper()
        clean_caus = causation_id.strip()
        with self._lock:
            self._last_event_by_order[clean_id] = clean_caus

    def get_last_event_id(self, order_id: str) -> str | None:
        """Return the last recorded audit event_id for an order, if any."""
        clean_id = order_id.strip().upper()
        with self._lock:
            return self._last_event_by_order.get(clean_id)

    def on_transition(self, event: TransitionEvent) -> None:
        """
        Record a state transition event into the ledger with unbroken causal lineage.
        Catches any persistence/ledger exception, stores it in _last_audit_error,
        and re-raises so the application layer can handle audit failures while ensuring
        the operational FSM state is not rolled back into an illegal configuration.
        """
        event_type = _STATE_TO_EVENT_TYPE.get(event.new_state, AuditEventType.ORDER_UNKNOWN)

        payload: dict[str, Any] = {
            "order_id": event.order_id,
            "previous_state": event.previous_state.value,
            "new_state": event.new_state.value,
            "triggering_event": event.event,
            "reason_code": event.reason_code.value,
            "reason": event.reason,
            "symbol": event.symbol,
            "signal_id": event.signal_id,
            "position_id": event.position_id,
            "filled_quantity": event.filled_quantity,
            "remaining_quantity": event.remaining_quantity,
            "average_price": f"{event.average_price:f}"
            if event.average_price is not None
            else None,
        }

        correlation_id = event.signal_id or event.order_id
        order_key = event.order_id.strip().upper()

        with self._lock:
            # Determine deterministic causal lineage for this logical order
            prev_event_id = self._last_event_by_order.get(order_key)
            if prev_event_id is None:
                # Check if ledger already has previous audit events for this order
                existing_events = self._ledger.get_events_by_entity("ORDER", order_key)
                if existing_events:
                    prev_event_id = existing_events[-1].event_id

            if prev_event_id is not None:
                causation_id = prev_event_id
            else:
                # Origin transition: deterministic origin marker (never fake UUID)
                causation_id = event.signal_id or f"ORIGIN:{order_key}"

            try:
                committed_event = self._ledger.append(
                    event_type=event_type,
                    entity_type="ORDER",
                    entity_id=event.order_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    payload=payload,
                    event_timestamp=event.timestamp,
                )
                self._last_event_by_order[order_key] = committed_event.event_id
                self._last_audit_error = None
            except Exception as e:
                self._last_audit_error = e
                raise


class ReconciliationAuditor:
    """
    Non-invasive observer that translates Phase 8 ColdBootReconciler facts into AuditLedger events.
    Supports ReconciliationObserver protocol and maintains unbroken causal event chains.
    """

    def __init__(self, ledger: AuditLedger) -> None:
        self._ledger = ledger
        self._last_audit_error: Exception | None = None
        self._lock = threading.RLock()
        self._start_event_by_recon: dict[str, str] = {}

    @property
    def last_audit_error(self) -> Exception | None:
        return self._last_audit_error

    def on_reconciliation_start(self, reconciliation_id: str) -> None:
        """Observer callback for reconciliation start."""
        self.audit_reconciliation_start(reconciliation_id)

    def on_reconciliation_result(self, result: ReconciliationResult) -> None:
        """Observer callback for reconciliation result."""
        self.audit_reconciliation_result(result)

    def __call__(self, result: ReconciliationResult) -> None:
        """Callable invocation support as direct reconciliation_listener."""
        self.audit_reconciliation_result(result)

    def audit_reconciliation_start(
        self,
        reconciliation_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuditEvent:
        """Record the initiation of a cold-boot or runtime reconciliation cycle."""
        clean_recon_id = reconciliation_id.strip().upper()
        corr_id = correlation_id.strip() if correlation_id else clean_recon_id
        caus_id = causation_id.strip() if causation_id else "COLD_BOOT_STARTUP"

        with self._lock:
            try:
                ev = self._ledger.append(
                    event_type=AuditEventType.RECONCILIATION_STARTED,
                    entity_type="RECONCILIATION",
                    entity_id=clean_recon_id,
                    correlation_id=corr_id,
                    causation_id=caus_id,
                    payload={"reconciliation_id": clean_recon_id},
                )
                self._start_event_by_recon[clean_recon_id] = ev.event_id
                self._last_audit_error = None
                return ev
            except Exception as e:
                self._last_audit_error = e
                raise

    def audit_reconciliation_result(
        self,
        result: ReconciliationResult,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuditEvent:
        """Record the outcome of a reconciliation pass and any hazard findings."""
        clean_recon_id = result.reconciliation_id.strip().upper()
        corr_id = correlation_id.strip() if correlation_id else clean_recon_id

        with self._lock:
            # Causation lineage: links to start event if available
            caus_id = (
                causation_id.strip()
                if causation_id
                else self._start_event_by_recon.get(clean_recon_id, clean_recon_id)
            )

            if result.status == ReconciliationStatus.MATCHED:
                event_type = AuditEventType.RECONCILIATION_MATCHED
            elif result.status == ReconciliationStatus.MISMATCH:
                event_type = AuditEventType.RECONCILIATION_MISMATCH
            elif result.status == ReconciliationStatus.ESCALATED:
                event_type = AuditEventType.MANUAL_ESCALATION
            else:
                event_type = AuditEventType.RECONCILIATION_FAILED

            payload: dict[str, Any] = {
                "reconciliation_id": clean_recon_id,
                "status": result.status.value,
                "reason_code": result.reason_code.value,
                "matched_count": result.matched_count,
                "mismatch_count": result.mismatch_count,
                "unknown_count": result.unknown_count,
                "new_entries_allowed": result.new_entries_allowed,
                "manual_escalation_required": result.manual_escalation_required,
            }

            try:
                res_event = self._ledger.append(
                    event_type=event_type,
                    entity_type="RECONCILIATION",
                    entity_id=clean_recon_id,
                    correlation_id=corr_id,
                    causation_id=caus_id,
                    payload=payload,
                    event_timestamp=result.timestamp,
                )

                # Audit individual emergency protection or protection unconfirmed findings
                for o_rec in result.order_details:
                    if o_rec.reason_code.value == "PROTECTION_UNCONFIRMED":
                        prot_ev = self._ledger.append(
                            event_type=AuditEventType.PROTECTION_UNCONFIRMED,
                            entity_type="ORDER",
                            entity_id=o_rec.client_order_id,
                            correlation_id=corr_id,
                            causation_id=res_event.event_id,
                            payload={
                                "client_order_id": o_rec.client_order_id,
                                "action": o_rec.action.value,
                                "reason_code": o_rec.reason_code.value,
                                "notes": o_rec.notes,
                            },
                            event_timestamp=result.timestamp,
                        )
                        self._ledger.append(
                            event_type=AuditEventType.EMERGENCY_PROTECTION_TRIGGERED,
                            entity_type="ORDER",
                            entity_id=o_rec.client_order_id,
                            correlation_id=corr_id,
                            causation_id=prot_ev.event_id,
                            payload={
                                "client_order_id": o_rec.client_order_id,
                                "action": o_rec.action.value,
                                "notes": o_rec.notes,
                            },
                            event_timestamp=result.timestamp,
                        )
                self._last_audit_error = None
                return res_event
            except Exception as e:
                self._last_audit_error = e
                raise
