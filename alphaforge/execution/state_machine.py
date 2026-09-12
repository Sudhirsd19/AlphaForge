"""
AlphaForge Order State Machine (17-State Lifecycle Machine).
Implements the formal deterministic finite state machine defined in the Master Specification.
Enforces explicit transition validation, terminal state protection, concurrency safety,
and complete transition event audit lineage.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.core.exceptions import IllegalStateTransitionError, OrderValidationError
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderEvent,
    OrderState,
)
from alphaforge.execution.models import (
    Order,
    TransitionEvent,
    TransitionResult,
)
from alphaforge.risk.enums import TradeSide

# --- Authoritative Legal Transition Matrix ---

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset(
        {
            OrderState.VALIDATED,
            OrderState.REJECTED,
        }
    ),
    OrderState.VALIDATED: frozenset(
        {
            OrderState.SUBMITTED,
            OrderState.CANCELLED,
        }
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.UNKNOWN,
            OrderState.REJECTED,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.UNKNOWN,
            OrderState.CANCELLED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.FILLED,
            OrderState.UNKNOWN,
            OrderState.CANCELLED,
        }
    ),
    OrderState.FILLED: frozenset(
        {
            OrderState.PROTECTION_PENDING,
        }
    ),
    OrderState.PROTECTION_PENDING: frozenset(
        {
            OrderState.PROTECTED,
            OrderState.MANUAL_ESCALATION,
        }
    ),
    OrderState.PROTECTED: frozenset(
        {
            OrderState.EXIT_PENDING,
        }
    ),
    OrderState.EXIT_PENDING: frozenset(
        {
            OrderState.CLOSED,
            OrderState.PARTIAL_EXIT,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIAL_EXIT: frozenset(
        {
            OrderState.EXIT_PENDING,
            OrderState.CLOSED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.RECONCILING,
        }
    ),
    OrderState.RECONCILING: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.CLOSED,
            OrderState.MANUAL_ESCALATION,
        }
    ),
    # Terminal states: zero forward transitions permitted
    OrderState.REJECTED: frozenset(),
    OrderState.CLOSED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.MANUAL_ESCALATION: frozenset(),
}

TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.REJECTED,
        OrderState.CLOSED,
        OrderState.CANCELLED,
        OrderState.MANUAL_ESCALATION,
    }
)

# Canonical mapping from (source, target) to default OrderEvent
DEFAULT_TRANSITION_EVENTS: dict[tuple[OrderState, OrderState], OrderEvent] = {
    (OrderState.CREATED, OrderState.VALIDATED): OrderEvent.VALIDATE_SUCCESS,
    (OrderState.CREATED, OrderState.REJECTED): OrderEvent.VALIDATE_REJECT,
    (OrderState.VALIDATED, OrderState.SUBMITTED): OrderEvent.SUBMIT,
    (OrderState.VALIDATED, OrderState.CANCELLED): OrderEvent.CANCEL,
    (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED): OrderEvent.ACKNOWLEDGE,
    (OrderState.SUBMITTED, OrderState.UNKNOWN): OrderEvent.COMMUNICATION_LOST,
    (OrderState.SUBMITTED, OrderState.REJECTED): OrderEvent.BROKER_REJECT,
    (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED): OrderEvent.PARTIAL_FILL,
    (OrderState.ACKNOWLEDGED, OrderState.FILLED): OrderEvent.FULL_FILL,
    (OrderState.ACKNOWLEDGED, OrderState.UNKNOWN): OrderEvent.COMMUNICATION_LOST,
    (OrderState.ACKNOWLEDGED, OrderState.CANCELLED): OrderEvent.CANCEL,
    (OrderState.PARTIALLY_FILLED, OrderState.FILLED): OrderEvent.FULL_FILL,
    (OrderState.PARTIALLY_FILLED, OrderState.UNKNOWN): OrderEvent.COMMUNICATION_LOST,
    (OrderState.PARTIALLY_FILLED, OrderState.CANCELLED): OrderEvent.CANCEL,
    (OrderState.FILLED, OrderState.PROTECTION_PENDING): OrderEvent.REQUEST_PROTECTION,
    (OrderState.PROTECTION_PENDING, OrderState.PROTECTED): OrderEvent.PROTECTION_CONFIRMED,
    (OrderState.PROTECTION_PENDING, OrderState.MANUAL_ESCALATION): OrderEvent.EMERGENCY_ESCALATE,
    (OrderState.PROTECTED, OrderState.EXIT_PENDING): OrderEvent.REQUEST_EXIT,
    (OrderState.EXIT_PENDING, OrderState.CLOSED): OrderEvent.EXIT_FULL_FILL,
    (OrderState.EXIT_PENDING, OrderState.PARTIAL_EXIT): OrderEvent.EXIT_PARTIAL_FILL,
    (OrderState.EXIT_PENDING, OrderState.UNKNOWN): OrderEvent.COMMUNICATION_LOST,
    (OrderState.PARTIAL_EXIT, OrderState.EXIT_PENDING): OrderEvent.REQUEST_EXIT,
    (OrderState.PARTIAL_EXIT, OrderState.CLOSED): OrderEvent.EXIT_FULL_FILL,
    (OrderState.PARTIAL_EXIT, OrderState.UNKNOWN): OrderEvent.COMMUNICATION_LOST,
    (OrderState.UNKNOWN, OrderState.RECONCILING): OrderEvent.START_RECONCILIATION,
    (OrderState.RECONCILING, OrderState.ACKNOWLEDGED): OrderEvent.RECONCILE_SUCCESS,
    (OrderState.RECONCILING, OrderState.FILLED): OrderEvent.RECONCILE_SUCCESS,
    (OrderState.RECONCILING, OrderState.CANCELLED): OrderEvent.RECONCILE_SUCCESS,
    (OrderState.RECONCILING, OrderState.CLOSED): OrderEvent.RECONCILE_SUCCESS,
    (OrderState.RECONCILING, OrderState.MANUAL_ESCALATION): OrderEvent.RECONCILE_FAILED,
}


class OrderStateMachine:
    """
    Authoritative Thread-Safe Finite State Machine for an individual order.
    Controls the entire lifecycle from CREATED through execution and protection to terminal state.
    """

    def __init__(
        self,
        order_id: str,
        symbol: str,
        side: TradeSide,
        quantity: int,
        initial_state: OrderState = OrderState.CREATED,
        signal_id: str | None = None,
        position_id: str | None = None,
        created_at: datetime | None = None,
        transition_listener: Callable[[TransitionEvent], None] | None = None,
    ) -> None:
        clean_id = order_id.strip().upper()
        if not clean_id:
            raise OrderValidationError("order_id must be non-empty and uppercase")
        clean_symbol = symbol.strip().upper()
        if not clean_symbol:
            raise OrderValidationError("symbol must be non-empty and uppercase")
        if quantity <= 0:
            raise OrderValidationError(f"quantity must be a strictly positive integer: {quantity}")

        now = created_at if created_at is not None else datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise OrderValidationError(f"created_at must be timezone-aware UTC: {now}")

        self._order_id: str = clean_id
        self._symbol: str = clean_symbol
        self._side: TradeSide = side
        self._quantity: int = quantity
        self._signal_id: str | None = signal_id.strip().upper() if signal_id else None
        self._position_id: str | None = position_id.strip().upper() if position_id else None
        self._transition_listener: Callable[[TransitionEvent], None] | None = transition_listener

        self._current_state: OrderState = initial_state
        self._filled_quantity: int = 0
        self._is_protection_confirmed: bool = False
        self._protection_order_id: str | None = None
        self._filled_at: datetime | None = None

        self._created_at: datetime = now
        self._updated_at: datetime = now
        self._history: list[TransitionEvent] = []
        self._lock: threading.RLock = threading.RLock()

    @property
    def order_id(self) -> str:
        return self._order_id

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def side(self) -> TradeSide:
        return self._side

    @property
    def quantity(self) -> int:
        return self._quantity

    @property
    def filled_quantity(self) -> int:
        with self._lock:
            return self._filled_quantity

    @property
    def remaining_quantity(self) -> int:
        with self._lock:
            return max(0, self._quantity - self._filled_quantity)

    @property
    def current_state(self) -> OrderState:
        with self._lock:
            return self._current_state

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._current_state in TERMINAL_STATES

    @property
    def is_protected(self) -> bool:
        with self._lock:
            return self._is_protection_confirmed and self._current_state == OrderState.PROTECTED

    @property
    def is_protection_confirmed(self) -> bool:
        with self._lock:
            return self._is_protection_confirmed

    @property
    def filled_at(self) -> datetime | None:
        with self._lock:
            return self._filled_at

    @property
    def history(self) -> tuple[TransitionEvent, ...]:
        with self._lock:
            return tuple(self._history)

    def can_transition(self, target_state: OrderState) -> bool:
        """Query whether transition from current_state to target_state is legally allowed."""
        with self._lock:
            if self._current_state in TERMINAL_STATES:
                return False
            allowed = ALLOWED_TRANSITIONS.get(self._current_state, frozenset())
            return target_state in allowed

    def snapshot(self) -> Order:
        """Produce an immutable snapshot of current order state."""
        with self._lock:
            return Order(
                order_id=self._order_id,
                state=self._current_state,
                symbol=self._symbol,
                side=self._side,
                quantity=self._quantity,
                filled_quantity=self._filled_quantity,
                signal_id=self._signal_id,
                position_id=self._position_id,
                is_protection_confirmed=self._is_protection_confirmed,
                protection_order_id=self._protection_order_id,
                filled_at=self._filled_at,
                created_at=self._created_at,
                updated_at=self._updated_at,
            )

    def transition(
        self,
        target_state: OrderState,
        event: OrderEvent | str | None = None,
        reason: str = "",
        timestamp: datetime | None = None,
        raise_on_error: bool = True,
        fill_qty: int | None = None,
        fill_price: Decimal | None = None,
        protection_order_id: str | None = None,
    ) -> TransitionResult:
        """
        Atomically validate and execute a state machine transition.

        Sequence:
        1. Acquire lock.
        2. Check for idempotent already-in-target-state request (NO-OP).
        3. Check terminal-state freeze.
        4. Validate target transition against ALLOWED_TRANSITIONS table.
        5. Validate preconditions and side effects.
        6. Create immutable TransitionEvent.
        7. Atomically mutate state and attributes.
        8. Return TransitionResult.
        """
        now = timestamp if timestamp is not None else datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise OrderValidationError(f"timestamp must be timezone-aware UTC: {now}")

        with self._lock:
            prev_state = self._current_state

            # 1. Idempotency check: Already in target state -> DUPLICATE_EVENT / NO-OP
            if target_state == prev_state:
                event_str = (
                    event.value if isinstance(event, OrderEvent) else (event or "IDEMPOTENT_NOOP")
                )
                return TransitionResult(
                    success=True,
                    order_id=self._order_id,
                    previous_state=prev_state,
                    current_state=prev_state,
                    event=event_str,
                    reason_code=ExecutionReasonCode.ALREADY_IN_TARGET_STATE,
                    reason=f"Order is already in state {target_state}",
                    transition_event=None,
                    timestamp=now,
                )

            # 2. Terminal state invariant: Terminal states cannot be resurrected
            if prev_state in TERMINAL_STATES:
                msg = (
                    f"Order '{self._order_id}' in terminal state '{prev_state}' cannot "
                    f"transition to '{target_state}'. Terminal states are permanent."
                )
                if raise_on_error:
                    raise IllegalStateTransitionError(msg)
                event_str = event.value if isinstance(event, OrderEvent) else (event or "INVALID")
                return TransitionResult(
                    success=False,
                    order_id=self._order_id,
                    previous_state=prev_state,
                    current_state=prev_state,
                    event=event_str,
                    reason_code=ExecutionReasonCode.TERMINAL_STATE,
                    reason=msg,
                    transition_event=None,
                    timestamp=now,
                )

            # 3. Transition legality check
            allowed_targets = ALLOWED_TRANSITIONS.get(prev_state, frozenset())
            if target_state not in allowed_targets:
                msg = (
                    f"Illegal transition for order '{self._order_id}': "
                    f"'{prev_state}' -> '{target_state}' is not permitted in authoritative matrix."
                )
                if raise_on_error:
                    raise IllegalStateTransitionError(msg)
                event_str = event.value if isinstance(event, OrderEvent) else (event or "ILLEGAL")
                return TransitionResult(
                    success=False,
                    order_id=self._order_id,
                    previous_state=prev_state,
                    current_state=prev_state,
                    event=event_str,
                    reason_code=ExecutionReasonCode.INVALID_TRANSITION,
                    reason=msg,
                    transition_event=None,
                    timestamp=now,
                )

            # 4. Resolve event name
            if event is not None:
                event_name = event.value if isinstance(event, OrderEvent) else str(event)
            else:
                default_evt = DEFAULT_TRANSITION_EVENTS.get((prev_state, target_state))
                event_name = default_evt.value if default_evt else f"TO_{target_state.value}"

            # 5. Precondition & state attribute updates
            if target_state == OrderState.FILLED:
                self._filled_quantity = self._quantity
                if self._filled_at is None:
                    self._filled_at = now
            elif target_state == OrderState.PARTIALLY_FILLED:
                if fill_qty is not None and fill_qty > 0:
                    self._filled_quantity = min(self._quantity, self._filled_quantity + fill_qty)
            elif target_state == OrderState.PROTECTED:
                self._is_protection_confirmed = True
                if protection_order_id:
                    self._protection_order_id = protection_order_id.strip().upper()

            # 6. Construct immutable transition event
            t_event = TransitionEvent(
                order_id=self._order_id,
                previous_state=prev_state,
                new_state=target_state,
                event=event_name,
                reason_code=ExecutionReasonCode.OK,
                reason=reason or f"Transitioned from {prev_state} to {target_state}",
                timestamp=now,
                signal_id=self._signal_id,
                symbol=self._symbol,
                position_id=self._position_id,
                filled_quantity=fill_qty,
                remaining_quantity=self.remaining_quantity,
                average_price=fill_price,
            )

            # 7. Atomic state mutation
            self._current_state = target_state
            self._updated_at = now
            self._history.append(t_event)

            # 7b. Non-invasive observer notification
            if self._transition_listener is not None:
                self._transition_listener(t_event)

            # 8. Return success result
            return TransitionResult(
                success=True,
                order_id=self._order_id,
                previous_state=prev_state,
                current_state=target_state,
                event=event_name,
                reason_code=ExecutionReasonCode.OK,
                reason=t_event.reason,
                transition_event=t_event,
                timestamp=now,
            )
