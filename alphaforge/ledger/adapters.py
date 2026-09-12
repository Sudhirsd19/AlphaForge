"""
AlphaForge Audit Ledger Adapters.
Provides non-invasive observers for Phase 7 Order FSM and Phase 8 Reconciliation.
Guarantees audit failure isolation: audit recording failures are surfaced to the application
without corrupting or mutating operational state machine transitions or reconciliation gates.
"""

from typing import Any

from alphaforge.execution.enums import OrderState
from alphaforge.execution.models import TransitionEvent
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
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
    """

    def __init__(self, ledger: AuditLedger) -> None:
        self._ledger = ledger
        self._last_audit_error: Exception | None = None

    @property
    def last_audit_error(self) -> Exception | None:
        return self._last_audit_error

    def on_transition(self, event: TransitionEvent) -> None:
        """
        Record a state transition event into the ledger.
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
        causation_id = event.event

        try:
            self._ledger.append(
                event_type=event_type,
                entity_type="ORDER",
                entity_id=event.order_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload=payload,
                event_timestamp=event.timestamp,
            )
            self._last_audit_error = None
        except Exception as e:
            self._last_audit_error = e
            raise


class ReconciliationAuditor:
    """
    Non-invasive observer that translates Phase 8 ColdBootReconciler facts into AuditLedger events.
    """

    def __init__(self, ledger: AuditLedger) -> None:
        self._ledger = ledger

    def audit_reconciliation_start(
        self,
        reconciliation_id: str,
        correlation_id: str | None = None,
    ) -> None:
        """Record the initiation of a cold-boot or runtime reconciliation cycle."""
        corr_id = correlation_id or reconciliation_id
        self._ledger.append(
            event_type=AuditEventType.RECONCILIATION_STARTED,
            entity_type="RECONCILIATION",
            entity_id=reconciliation_id,
            correlation_id=corr_id,
            causation_id="COLD_BOOT_STARTUP",
            payload={"reconciliation_id": reconciliation_id},
        )

    def audit_reconciliation_result(
        self,
        result: ReconciliationResult,
        correlation_id: str | None = None,
    ) -> None:
        """Record the outcome of a reconciliation pass."""
        corr_id = correlation_id or result.reconciliation_id
        causation_id = result.reconciliation_id

        if result.status == ReconciliationStatus.MATCHED:
            event_type = AuditEventType.RECONCILIATION_MATCHED
        elif result.status == ReconciliationStatus.MISMATCH:
            event_type = AuditEventType.RECONCILIATION_MISMATCH
        elif result.status == ReconciliationStatus.ESCALATED:
            event_type = AuditEventType.MANUAL_ESCALATION
        else:
            event_type = AuditEventType.RECONCILIATION_FAILED

        payload: dict[str, Any] = {
            "reconciliation_id": result.reconciliation_id,
            "status": result.status.value,
            "reason_code": result.reason_code.value,
            "matched_count": result.matched_count,
            "mismatch_count": result.mismatch_count,
            "unknown_count": result.unknown_count,
            "new_entries_allowed": result.new_entries_allowed,
            "manual_escalation_required": result.manual_escalation_required,
        }

        self._ledger.append(
            event_type=event_type,
            entity_type="RECONCILIATION",
            entity_id=result.reconciliation_id,
            correlation_id=corr_id,
            causation_id=causation_id,
            payload=payload,
            event_timestamp=result.timestamp,
        )

        # Audit individual emergency protection or protection unconfirmed findings
        for o_rec in result.order_details:
            if o_rec.reason_code.value == "PROTECTION_UNCONFIRMED":
                self._ledger.append(
                    event_type=AuditEventType.PROTECTION_UNCONFIRMED,
                    entity_type="ORDER",
                    entity_id=o_rec.client_order_id,
                    correlation_id=corr_id,
                    causation_id=result.reconciliation_id,
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
                    causation_id=f"PROT-UNCONFIRMED-{o_rec.client_order_id}",
                    payload={
                        "client_order_id": o_rec.client_order_id,
                        "action": o_rec.action.value,
                        "notes": o_rec.notes,
                    },
                    event_timestamp=result.timestamp,
                )
