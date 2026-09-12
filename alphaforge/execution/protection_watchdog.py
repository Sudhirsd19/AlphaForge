"""
AlphaForge Emergency Protection Watchdog.
Implements the Emergency Unprotected Position Protocol (P0 Hazard Safety Rule).
Inspects active positions and orders, detects unconfirmed or timed-out protection,
and generates deterministic, broker-independent EmergencyExitCommands.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from alphaforge.core.exceptions import OrderValidationError
from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderSide,
    OrderState,
    WatchdogStatus,
)
from alphaforge.execution.models import (
    EmergencyExitCommand,
    Order,
    WatchdogDecision,
)
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.risk.enums import TradeSide


class ProtectionConfig(BaseModel):
    """
    Configuration parameters for the Emergency Protection Watchdog.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_protection_delay_seconds: Decimal = Field(
        default=Decimal("5.0"),
        gt=Decimal("0"),
        description="Maximum seconds allowed in PROTECTION_PENDING before emergency escalation",
    )


class ProtectionWatchdog:
    """
    Synchronous, deterministic watchdog evaluating order and position protection safety.
    Enforces the core rule:
      FILLED position + confirmed resting stop absent = EMERGENCY UNPROTECTED POSITION
    """

    def __init__(self, config: ProtectionConfig | None = None) -> None:
        self._config = config if config is not None else ProtectionConfig()

    @property
    def config(self) -> ProtectionConfig:
        return self._config

    def evaluate_order(
        self,
        order: Order,
        current_time: datetime | None = None,
    ) -> WatchdogDecision:
        """
        Evaluate an Order snapshot for protection invariants.
        Returns an immutable WatchdogDecision.
        """
        now = current_time if current_time is not None else datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise OrderValidationError(f"current_time must be timezone-aware UTC: {now}")

        order_id = order.order_id
        state = order.state

        # Pre-fill states: No position exists yet -> SAFE from unprotected position hazard
        if state in (
            OrderState.CREATED,
            OrderState.VALIDATED,
            OrderState.REJECTED,
            OrderState.SUBMITTED,
            OrderState.ACKNOWLEDGED,
            OrderState.CANCELLED,
            OrderState.CLOSED,
        ):
            return WatchdogDecision(
                status=WatchdogStatus.SAFE,
                order_id=order_id,
                current_state=state,
                is_protected=False,
                is_emergency=False,
                reason_code=ExecutionReasonCode.OK,
                reason=f"No open position for order in state '{state}'",
                emergency_command=None,
                timestamp=now,
            )

        # Confirmed protected state
        if state == OrderState.PROTECTED:
            if not order.is_protection_confirmed:
                # Metadata discrepancy: State claims PROTECTED but confirmation flag is False
                return self._create_escalation_decision(
                    order=order,
                    reason_code=ExecutionReasonCode.MISSING_REQUIRED_CONFIRMATION,
                    reason=(
                        f"Order '{order_id}' in state PROTECTED has "
                        f"is_protection_confirmed=False (metadata discrepancy)"
                    ),
                    timestamp=now,
                )
            return WatchdogDecision(
                status=WatchdogStatus.SAFE,
                order_id=order_id,
                current_state=state,
                is_protected=True,
                is_emergency=False,
                reason_code=ExecutionReasonCode.OK,
                reason=f"Position for order '{order_id}' is confirmed protected",
                emergency_command=None,
                timestamp=now,
            )

        # Position exists: FILLED, PARTIALLY_FILLED, PROTECTION_PENDING, EXIT_PENDING,
        # PARTIAL_EXIT, UNKNOWN, RECONCILING, MANUAL_ESCALATION
        # 1. Validate quantity safety
        fill_qty = order.filled_quantity if order.filled_quantity > 0 else order.quantity
        if fill_qty <= 0:
            return self._create_escalation_decision(
                order=order,
                reason_code=ExecutionReasonCode.INVALID_QUANTITY,
                reason=f"Order '{order_id}' has non-positive quantity: {fill_qty}",
                timestamp=now,
            )

        # 2. Unknown or Reconciling or Manual Escalation -> Emergency state
        if state in (OrderState.UNKNOWN, OrderState.RECONCILING, OrderState.MANUAL_ESCALATION):
            return self._generate_emergency_decision(
                order=order,
                status=WatchdogStatus.ESCALATED,
                reason_code=ExecutionReasonCode.UNKNOWN_STATE,
                reason=f"Order '{order_id}' is in unconfirmed/escalated state '{state}'",
                quantity=fill_qty,
                timestamp=now,
            )

        # 3. FILLED: Position is active but stop-loss order has not even entered pending/confirmed
        if state == OrderState.FILLED:
            return self._generate_emergency_decision(
                order=order,
                status=WatchdogStatus.UNPROTECTED_HAZARD,
                reason_code=ExecutionReasonCode.UNPROTECTED_POSITION,
                reason=(
                    f"Order '{order_id}' is FILLED without confirmed resting stop protection. "
                    f"Must transition to PROTECTION_PENDING immediately."
                ),
                quantity=fill_qty,
                timestamp=now,
            )

        # 4. PROTECTION_PENDING: Stop-loss dispatched, awaiting confirmation
        if state == OrderState.PROTECTION_PENDING:
            # Check for timeout
            ref_time = order.filled_at if order.filled_at is not None else order.updated_at
            elapsed = (now - ref_time).total_seconds()
            max_delay = float(self._config.max_protection_delay_seconds)

            if elapsed > max_delay:
                # Timeout breached! P0 Emergency Protocol
                return self._generate_emergency_decision(
                    order=order,
                    status=WatchdogStatus.TIMEOUT_BREACH,
                    reason_code=ExecutionReasonCode.TIMEOUT_EXCEEDED,
                    reason=(
                        f"Order '{order_id}' exceeded protection timeout "
                        f"({elapsed:.2f}s > {max_delay:.2f}s). Triggering emergency exit."
                    ),
                    quantity=fill_qty,
                    timestamp=now,
                )

            # Within timeout window: Pending, but NOT yet protected!
            return WatchdogDecision(
                status=WatchdogStatus.UNPROTECTED_HAZARD,
                order_id=order_id,
                current_state=state,
                is_protected=False,
                is_emergency=False,
                reason_code=ExecutionReasonCode.PROTECTION_REQUIRED,
                reason=(
                    f"Order '{order_id}' is awaiting stop confirmation "
                    f"({elapsed:.2f}s / {max_delay:.2f}s). Normal entries blocked."
                ),
                emergency_command=None,
                timestamp=now,
            )

        # 5. Default fallback for other active states without protection
        return WatchdogDecision(
            status=WatchdogStatus.SAFE,
            order_id=order_id,
            current_state=state,
            is_protected=order.is_protection_confirmed,
            is_emergency=False,
            reason_code=ExecutionReasonCode.OK,
            reason=f"Order '{order_id}' in state '{state}' evaluated",
            emergency_command=None,
            timestamp=now,
        )

    def evaluate_fsm(
        self,
        fsm: OrderStateMachine,
        current_time: datetime | None = None,
    ) -> WatchdogDecision:
        """
        Evaluate an active OrderStateMachine instance.
        Takes a thread-safe snapshot and delegates to evaluate_order.
        """
        snapshot = fsm.snapshot()
        return self.evaluate_order(snapshot, current_time=current_time)

    def _generate_emergency_decision(
        self,
        order: Order,
        status: WatchdogStatus,
        reason_code: ExecutionReasonCode,
        reason: str,
        quantity: int,
        timestamp: datetime,
    ) -> WatchdogDecision:
        """Helper to create an EmergencyExitCommand and associated WatchdogDecision."""
        exit_side = OrderSide.SELL if order.side == TradeSide.LONG else OrderSide.BUY
        pos_id = order.position_id if order.position_id else f"POS-{order.order_id}"
        cmd_id = f"EMERG-EXIT-{order.order_id}-{int(timestamp.timestamp())}"

        cmd = EmergencyExitCommand(
            command_id=cmd_id,
            order_id=order.order_id,
            position_id=pos_id,
            symbol=order.symbol,
            side=exit_side,
            position_side=order.side,
            quantity=quantity,
            reason=reason,
            created_at=timestamp,
        )

        return WatchdogDecision(
            status=status,
            order_id=order.order_id,
            current_state=order.state,
            is_protected=False,
            is_emergency=True,
            reason_code=reason_code,
            reason=reason,
            emergency_command=cmd,
            timestamp=timestamp,
        )

    def _create_escalation_decision(
        self,
        order: Order,
        reason_code: ExecutionReasonCode,
        reason: str,
        timestamp: datetime,
    ) -> WatchdogDecision:
        """Helper for escalation decisions where emergency command cannot be safely generated."""
        return WatchdogDecision(
            status=WatchdogStatus.ESCALATED,
            order_id=order.order_id,
            current_state=order.state,
            is_protected=False,
            is_emergency=True,
            reason_code=reason_code,
            reason=reason,
            emergency_command=None,
            timestamp=timestamp,
        )
