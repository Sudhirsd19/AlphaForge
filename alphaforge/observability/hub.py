"""
AlphaForge Observability Hub and Runtime Adapters.

Coordinates passive event observation, metric increments, and health tracking
across active runtime execution without altering trading semantics.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from alphaforge.observability.context import TraceContext
from alphaforge.observability.events import (
    DataEventType,
    FillEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    ObservabilitySeverity,
    OrderEventType,
    ReconciliationEventType,
    RiskEventType,
    SecurityEventType,
    StrategyEventType,
)
from alphaforge.observability.health import HealthAggregator
from alphaforge.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

    from alphaforge.broker.models import BrokerOrder, BrokerOrderRequest
    from alphaforge.core.models import StrategySignal
    from alphaforge.execution.models import TransitionEvent
    from alphaforge.observability.sinks import SafeObservabilityDispatcher
    from alphaforge.reconciliation.models import ReconciliationResult
    from alphaforge.risk.models import RiskDecision, RiskInput

_hub_lock = threading.RLock()
_global_dispatcher: SafeObservabilityDispatcher | None = None
_global_metrics: MetricsRegistry = MetricsRegistry()
_global_health: HealthAggregator = HealthAggregator()


def get_global_dispatcher() -> SafeObservabilityDispatcher | None:
    """Retrieve active global dispatcher, if registered."""
    with _hub_lock:
        return _global_dispatcher


def set_global_dispatcher(dispatcher: SafeObservabilityDispatcher | None) -> None:
    """Register or deregister global observability dispatcher."""
    with _hub_lock:
        global _global_dispatcher
        _global_dispatcher = dispatcher


def get_global_metrics() -> MetricsRegistry:
    """Retrieve global metrics registry."""
    with _hub_lock:
        return _global_metrics


def get_global_health() -> HealthAggregator:
    """Retrieve global health aggregator."""
    with _hub_lock:
        return _global_health


def reset_observability_hub() -> None:
    """Reset global dispatcher, metrics, and health aggregator (for test cleanup)."""
    with _hub_lock:
        global _global_dispatcher, _global_metrics, _global_health
        _global_dispatcher = None
        _global_metrics = MetricsRegistry()
        _global_health = HealthAggregator()
        TraceContext.clear()


def observe_event(event: ObservabilityEvent) -> None:
    """
    Safely emit an event to the global dispatcher, if active.
    Guaranteed no-op if no dispatcher is registered.
    Guaranteed never to raise exceptions or interrupt trading logic.
    """
    with _hub_lock:
        disp = _global_dispatcher
    if disp is not None:
        try:
            disp.emit(event)
            _global_metrics.increment("observability.events_emitted")
        except Exception:
            _global_metrics.increment("observability.dispatcher_failures")


def observe_event_safely(factory: Callable[[], ObservabilityEvent]) -> None:
    """
    Lazily evaluate and emit an observability event only if a dispatcher is active.
    If no dispatcher is configured, avoids all event allocation overhead.
    """
    with _hub_lock:
        disp = _global_dispatcher
    if disp is not None:
        try:
            event = factory()
            disp.emit(event)
            _global_metrics.increment("observability.events_emitted")
        except Exception:
            _global_metrics.increment("observability.dispatcher_failures")


def observe_data_normalized(
    symbol: str | None,
    valid_count: int,
    quarantined_count: int,
    duplicates_count: int,
    gap_count: int,
    quality_status: str,
    was_out_of_order: bool,
) -> None:
    """Passive hook for data normalization batch."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        observe_event(
            ObservabilityEvent(
                event_type=DataEventType.DATA_RECEIVED.value,
                category=ObservabilityCategory.DATA,
                severity=ObservabilitySeverity.INFO,
                symbol=symbol,
                message=f"Normalized {valid_count} candles (status: {quality_status})",
                attributes={
                    "valid_count": valid_count,
                    "quarantined_count": quarantined_count,
                    "duplicates_count": duplicates_count,
                    "gap_count": gap_count,
                    "quality_status": quality_status,
                    "was_out_of_order": was_out_of_order,
                },
            )
        )
        get_global_metrics().increment("data.events_received", valid_count)
        if gap_count > 0:
            observe_event(
                ObservabilityEvent(
                    event_type=DataEventType.DATA_GAP.value,
                    category=ObservabilityCategory.DATA,
                    severity=ObservabilitySeverity.WARNING,
                    symbol=symbol,
                    message=f"Detected {gap_count} data gap(s)",
                    attributes={"gap_count": gap_count},
                )
            )
            get_global_metrics().increment("data.data_gaps", gap_count)
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_strategy_decision(signal: StrategySignal) -> None:
    """Passive hook for DeterministicStrategyEngine evaluation."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        from alphaforge.core.enums import StrategyDecision

        is_accept = signal.decision == StrategyDecision.ACCEPT
        evt_type = (
            StrategyEventType.SIGNAL_ACCEPTED.value
            if is_accept
            else StrategyEventType.SIGNAL_REJECTED.value
        )
        severity = ObservabilitySeverity.INFO if is_accept else ObservabilitySeverity.WARNING
        msg = f"Strategy decision: {signal.decision.value} ({signal.rejection_code.value})"
        observe_event(
            ObservabilityEvent(
                event_type=evt_type,
                category=ObservabilityCategory.STRATEGY,
                severity=severity,
                symbol=signal.symbol,
                correlation_id=signal.signal_id,
                message=msg,
                attributes={
                    "decision": signal.decision.value,
                    "direction": signal.direction.value,
                    "rejection_code": signal.rejection_code.value,
                    "entry_reference": str(signal.entry_reference),
                    "stop_reference": str(signal.stop_reference),
                    "target_reference": str(signal.target_reference),
                },
            )
        )
        get_global_metrics().increment("strategy.signals_generated")
        if is_accept:
            get_global_metrics().increment("strategy.signals_accepted")
        else:
            get_global_metrics().increment("strategy.signals_rejected")
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_risk_evaluation(decision: RiskDecision, trade_input: RiskInput) -> None:
    """Passive hook for pre-trade risk evaluation."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        from alphaforge.risk.enums import RiskDecisionState

        is_approved = decision.decision == RiskDecisionState.APPROVED
        evt_type = (
            RiskEventType.RISK_ACCEPTED.value if is_approved else RiskEventType.RISK_REJECTED.value
        )
        severity = ObservabilitySeverity.INFO if is_approved else ObservabilitySeverity.WARNING
        risk_amt_str = str(decision.risk_amount) if decision.risk_amount is not None else None
        notional_str = str(decision.notional) if decision.notional is not None else None

        observe_event(
            ObservabilityEvent(
                event_type=evt_type,
                category=ObservabilityCategory.RISK,
                severity=severity,
                symbol=trade_input.symbol,
                correlation_id=trade_input.signal_id,
                message=f"Risk decision: {decision.decision.value} ({decision.reason_code.value})",
                attributes={
                    "decision": decision.decision.value,
                    "reason_code": decision.reason_code.value,
                    "reason": decision.reason,
                    "risk_amount": risk_amt_str,
                    "notional": notional_str,
                },
            )
        )
        get_global_metrics().increment("risk.risk_checks")
        if is_approved:
            get_global_metrics().increment("risk.risk_reservations")
        else:
            get_global_metrics().increment("risk.risk_rejections")
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_security_evaluation(
    client_order_id: str,
    symbol: str,
    mode_val: str,
    exc: Exception | None = None,
) -> None:
    """Passive hook for SecurityAuthorizer check."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        if exc is not None:
            observe_event(
                ObservabilityEvent(
                    event_type=SecurityEventType.SECURITY_REJECTED.value,
                    category=ObservabilityCategory.SECURITY,
                    severity=ObservabilitySeverity.CRITICAL,
                    symbol=symbol,
                    mode=mode_val,
                    client_order_id=client_order_id,
                    message=f"Order {client_order_id} rejected by security: {type(exc).__name__}",
                    attributes={
                        "client_order_id": client_order_id,
                        "error_type": type(exc).__name__,
                        "reason": str(exc),
                    },
                )
            )
        else:
            observe_event(
                ObservabilityEvent(
                    event_type=SecurityEventType.SECURITY_CHECK.value,
                    category=ObservabilityCategory.SECURITY,
                    severity=ObservabilitySeverity.INFO,
                    symbol=symbol,
                    mode=mode_val,
                    client_order_id=client_order_id,
                    message=f"Order {client_order_id} authorized across security gates",
                    attributes={"client_order_id": client_order_id, "status": "AUTHORIZED"},
                )
            )
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_order_submission(request: BrokerOrderRequest) -> None:
    """Passive hook for SecureBroker submit_order."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        observe_event(
            ObservabilityEvent(
                event_type=OrderEventType.ORDER_SUBMIT.value,
                category=ObservabilityCategory.ORDER,
                symbol=request.symbol,
                client_order_id=request.client_order_id,
                message=(
                    f"Submitting order {request.client_order_id}: {request.side.value} "
                    f"{request.quantity} @ {request.order_type.value}"
                ),
                attributes={
                    "client_order_id": request.client_order_id,
                    "symbol": request.symbol,
                    "side": request.side.value,
                    "quantity": request.quantity,
                    "order_type": request.order_type.value,
                    "role": request.role.value,
                },
            )
        )
        get_global_metrics().increment("order.orders_submitted")
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_order_ack(order: BrokerOrder) -> None:
    """Passive hook for SecureBroker order acknowledgement."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        observe_event(
            ObservabilityEvent(
                event_type=OrderEventType.ORDER_ACK.value,
                category=ObservabilityCategory.ORDER,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                broker_order_id=order.broker_order_id,
                message=(
                    f"Order {order.client_order_id} acknowledged as {order.broker_order_id} "
                    f"(status: {order.status.value})"
                ),
                attributes={
                    "client_order_id": order.client_order_id,
                    "broker_order_id": order.broker_order_id,
                    "status": order.status.value,
                    "filled_quantity": order.filled_quantity,
                },
            )
        )
        get_global_metrics().increment("order.orders_acknowledged")
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


def observe_order_submit_failure(request: BrokerOrderRequest, exc: Exception) -> None:
    """Passive hook for SecureBroker submission failures."""
    disp = get_global_dispatcher()
    if disp is None:
        return

    try:
        from alphaforge.core.exceptions import BrokerUnavailableError

        is_timeout = isinstance(exc, BrokerUnavailableError) or "timeout" in str(exc).lower()
        evt_type = (
            OrderEventType.ORDER_TIMEOUT.value if is_timeout else OrderEventType.ORDER_REJECT.value
        )
        observe_event(
            ObservabilityEvent(
                event_type=evt_type,
                category=ObservabilityCategory.ORDER,
                severity=ObservabilitySeverity.ERROR,
                symbol=request.symbol,
                client_order_id=request.client_order_id,
                message=f"Order {request.client_order_id} submission failed: {exc}",
                attributes={
                    "client_order_id": request.client_order_id,
                    "error": str(exc),
                    "is_timeout": is_timeout,
                },
            )
        )
        if is_timeout:
            get_global_metrics().increment("order.orders_timed_out")
        else:
            get_global_metrics().increment("order.orders_rejected")
    except Exception:
        get_global_metrics().increment("observability.dispatcher_failures")


class OrderFSMObservabilityAdapter:
    """
    Non-invasive observer adapter for Phase 7 OrderStateMachine transitions.
    Translates FSM state transitions into structured ORDER / FILL ObservabilityEvents.
    """

    def __init__(self, dispatcher: SafeObservabilityDispatcher | None = None) -> None:
        self._dispatcher = dispatcher

    def __call__(self, event: TransitionEvent) -> None:
        self.on_transition(event)

    def on_transition(self, event: TransitionEvent) -> None:
        """Handle OrderStateMachine TransitionEvent."""
        disp = self._dispatcher or get_global_dispatcher()
        if disp is None:
            return

        from alphaforge.execution.enums import OrderState

        event_type = OrderEventType.ORDER_UNKNOWN.value
        category = ObservabilityCategory.ORDER
        severity = ObservabilitySeverity.INFO

        if event.new_state == OrderState.SUBMITTED:
            event_type = OrderEventType.ORDER_SUBMIT.value
        elif event.new_state == OrderState.ACKNOWLEDGED:
            event_type = OrderEventType.ORDER_ACK.value
        elif event.new_state == OrderState.REJECTED:
            event_type = OrderEventType.ORDER_REJECT.value
            severity = ObservabilitySeverity.WARNING
        elif event.new_state == OrderState.CANCELLED:
            event_type = OrderEventType.ORDER_CANCEL.value
        elif event.new_state == OrderState.UNKNOWN:
            event_type = OrderEventType.ORDER_UNKNOWN.value
            severity = ObservabilitySeverity.ERROR
        elif event.new_state in (OrderState.PARTIALLY_FILLED, OrderState.FILLED):
            event_type = FillEventType.FILL_RECEIVED.value
            category = ObservabilityCategory.FILL

        msg = (
            f"Order {event.order_id} transition: {event.previous_state.value} -> "
            f"{event.new_state.value} ({event.event})"
        )
        avg_p = str(event.average_price) if event.average_price is not None else None

        obs_event = ObservabilityEvent(
            event_type=event_type,
            category=category,
            severity=severity,
            symbol=event.symbol,
            client_order_id=event.order_id,
            correlation_id=event.signal_id or TraceContext.get_correlation_id() or event.order_id,
            causation_id=TraceContext.get_causation_id(),
            message=msg,
            attributes={
                "previous_state": event.previous_state.value,
                "new_state": event.new_state.value,
                "event": event.event,
                "reason": event.reason,
                "reason_code": event.reason_code.value,
                "filled_quantity": event.filled_quantity,
                "remaining_quantity": event.remaining_quantity,
                "average_price": avg_p,
            },
        )

        try:
            disp.emit(obs_event)
            get_global_metrics().increment("observability.events_emitted")
            if event_type == OrderEventType.ORDER_SUBMIT.value:
                get_global_metrics().increment("order.orders_submitted")
            elif event_type == OrderEventType.ORDER_ACK.value:
                get_global_metrics().increment("order.orders_acknowledged")
            elif event_type == OrderEventType.ORDER_REJECT.value:
                get_global_metrics().increment("order.orders_rejected")
            elif event_type == FillEventType.FILL_RECEIVED.value:
                get_global_metrics().increment("fill.fills_received")
        except Exception:
            get_global_metrics().increment("observability.dispatcher_failures")


class ReconciliationObservabilityAdapter:
    """
    Non-invasive observer adapter for Phase 8 ColdBootReconciler.
    Translates reconciliation lifecycle events and results into RECONCILIATION ObservabilityEvents.
    """

    def __init__(self, dispatcher: SafeObservabilityDispatcher | None = None) -> None:
        self._dispatcher = dispatcher

    def on_start(self, recon_id: str) -> None:
        """Handle reconciliation start."""
        disp = self._dispatcher or get_global_dispatcher()
        if disp is None:
            return

        event = ObservabilityEvent(
            event_type=ReconciliationEventType.RECONCILIATION_START.value,
            category=ObservabilityCategory.RECONCILIATION,
            severity=ObservabilitySeverity.INFO,
            correlation_id=recon_id,
            causation_id=TraceContext.get_causation_id(),
            message=f"Reconciliation run {recon_id} started",
            attributes={"reconciliation_id": recon_id},
        )
        try:
            disp.emit(event)
            get_global_metrics().increment("reconciliation.reconciliation_runs")
            get_global_metrics().increment("observability.events_emitted")
        except Exception:
            get_global_metrics().increment("observability.dispatcher_failures")

    def on_result(self, result: ReconciliationResult) -> None:
        """Handle reconciliation result."""
        disp = self._dispatcher or get_global_dispatcher()
        if disp is None:
            return

        from alphaforge.reconciliation.models import ReconciliationStatus

        is_success = result.status == ReconciliationStatus.MATCHED
        severity = ObservabilitySeverity.INFO if is_success else ObservabilitySeverity.WARNING

        event_type = (
            ReconciliationEventType.RECONCILIATION_SUCCESS.value
            if is_success
            else ReconciliationEventType.RECONCILIATION_MISMATCH.value
        )

        msg = f"Reconciliation {result.reconciliation_id} completed: {result.status.value}"

        event = ObservabilityEvent(
            event_type=event_type,
            category=ObservabilityCategory.RECONCILIATION,
            severity=severity,
            correlation_id=result.reconciliation_id,
            causation_id=TraceContext.get_causation_id(),
            message=msg,
            attributes={
                "reconciliation_id": result.reconciliation_id,
                "status": result.status.value,
                "reason_code": result.reason_code.value,
                "mismatch_count": result.mismatch_count,
            },
        )
        try:
            disp.emit(event)
            get_global_metrics().increment("observability.events_emitted")
            if not is_success:
                get_global_metrics().increment("reconciliation.reconciliation_mismatches")
        except Exception:
            get_global_metrics().increment("observability.dispatcher_failures")

    def __call__(self, result: ReconciliationResult) -> None:
        self.on_result(result)
