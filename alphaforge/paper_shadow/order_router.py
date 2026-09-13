"""
AlphaForge Order Lifecycle & Execution Router.

Coordinates order intent registration in IdempotencyRegistry, 17-state FSM lifecycle transitions,
and execution submission to Phase 15 DeploymentBrokerGuard.
In SHADOW mode, strictly enforces zero broker submissions while maintaining virtual order lifecycle.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderType,
)
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.exceptions import OrderValidationError
from alphaforge.deployment.broker_guard import DeploymentBrokerGuard
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.execution.enums import (
    OrderEvent,
    OrderSide,
    OrderState,
)
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_client_order_id,
)
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.risk.enums import TradeSide

if TYPE_CHECKING:
    from decimal import Decimal

    from alphaforge.broker.interface import AbstractBroker
    from alphaforge.execution.models import Order
    from alphaforge.paper_shadow.models import PaperShadowConfig


class PaperShadowOrderRouter:
    """
    State-aware order lifecycle router for Paper / Shadow trading.
    Binds frozen OrderStateMachine, IdempotencyRegistry, and DeploymentBrokerGuard.
    """

    def __init__(
        self,
        config: PaperShadowConfig,
        broker: AbstractBroker | None = None,
        idempotency_registry: IdempotencyRegistry | None = None,
    ) -> None:
        self._config = config
        self._mode = config.mode
        self._lock = threading.RLock()

        self._idempotency = idempotency_registry or IdempotencyRegistry()
        self._state_machines: dict[str, OrderStateMachine] = {}

        # Set up broker execution boundary
        if self._mode == PaperShadowMode.PAPER:
            raw_broker = broker if broker is not None else PaperBroker()
            # Wrap in Phase 15 DeploymentBrokerGuard to enforce safety invariants
            deployment_cfg = config.deployment_config
            if deployment_cfg.environment != DeploymentEnvironment.PAPER:
                deployment_cfg = DeploymentConfig(
                    environment=DeploymentEnvironment.PAPER,
                    runtime_root=deployment_cfg.runtime_root,
                )
            self._guarded_broker: AbstractBroker | None = DeploymentBrokerGuard(
                delegate=raw_broker,
                config=deployment_cfg,
            )
        else:
            # SHADOW mode: strictly no broker execution
            self._guarded_broker = None

    @property
    def mode(self) -> PaperShadowMode:
        return self._mode

    @property
    def idempotency_registry(self) -> IdempotencyRegistry:
        return self._idempotency

    @property
    def guarded_broker(self) -> AbstractBroker | None:
        return self._guarded_broker

    def create_and_route_order(
        self,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        role: OrderRole,
        signal_id: str,
        side: TradeSide,
        quantity: int,
        order_type: BrokerOrderType = BrokerOrderType.MARKET,
        limit_price: Decimal | None = None,
        trigger_price: Decimal | None = None,
    ) -> tuple[Order, BrokerOrder | None]:
        """
        Create, register, and route an order intent through the authoritative FSM.
        In PAPER mode: Submits order request to DeploymentBrokerGuard.
        In SHADOW mode: Records virtual intent and simulates FSM without submitting to broker.
        """
        with self._lock:
            # 1. Deterministic client_order_id generation
            client_order_id = generate_client_order_id(
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                symbol=symbol,
                role=role,
                signal_id=signal_id,
            )

            # 2. Register intent for idempotency
            order_intent = OrderIntent(
                client_order_id=client_order_id,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                symbol=symbol,
                role=role,
                signal_id=signal_id,
                side=side,
                quantity=quantity,
            )
            self._idempotency.register(order_intent)

            # 3. Initialize Order State Machine
            order_side = OrderSide.BUY if side == TradeSide.LONG else OrderSide.SELL
            now = datetime.now(UTC)
            fsm = OrderStateMachine(
                order_id=client_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                initial_state=OrderState.CREATED,
                signal_id=signal_id,
                created_at=now,
            )
            self._state_machines[client_order_id] = fsm

            # 4. FSM transition: CREATED -> VALIDATED
            fsm.transition(
                target_state=OrderState.VALIDATED,
                event=OrderEvent.VALIDATE_SUCCESS,
                reason="Pre-trade validation passed",
            )

            # 5. FSM transition: VALIDATED -> SUBMITTED
            fsm.transition(
                target_state=OrderState.SUBMITTED,
                event=OrderEvent.SUBMIT,
                reason=f"Routing mode {self._mode.value}",
            )

            broker_order: BrokerOrder | None = None

            # 6. PAPER vs SHADOW Execution Routing
            if self._mode == PaperShadowMode.PAPER:
                assert self._guarded_broker is not None
                req = BrokerOrderRequest(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=order_side,
                    quantity=quantity,
                    role=role,
                    order_type=order_type,
                    price=limit_price,
                    trigger_price=trigger_price,
                )
                # Submit through DeploymentBrokerGuard
                broker_order = self._guarded_broker.submit_order(req)

                # FSM transition: SUBMITTED -> ACKNOWLEDGED
                fsm.transition(
                    target_state=OrderState.ACKNOWLEDGED,
                    event=OrderEvent.ACKNOWLEDGE,
                    reason=f"Broker acknowledged {broker_order.broker_order_id}",
                )
            else:
                # SHADOW mode: virtual acknowledgement, ZERO broker calls
                fsm.transition(
                    target_state=OrderState.ACKNOWLEDGED,
                    event=OrderEvent.ACKNOWLEDGE,
                    reason="Shadow virtual acknowledgement",
                )

            return fsm.snapshot(), broker_order

    def record_fill_transition(
        self,
        client_order_id: str,
        fill_quantity: int,
        fill_price: Decimal,
        is_full_fill: bool,
    ) -> Order:
        """
        Record fill on the Order State Machine.
        Handles partial fill (ACKNOWLEDGED -> PARTIALLY_FILLED)
        and full fill (PARTIALLY_FILLED/ACKNOWLEDGED -> FILLED).
        """
        with self._lock:
            fsm = self._state_machines.get(client_order_id)
            if fsm is None:
                raise OrderValidationError(f"Order '{client_order_id}' not found in state machine")

            if is_full_fill:
                fsm.transition(
                    target_state=OrderState.FILLED,
                    event=OrderEvent.FULL_FILL,
                    reason="Full fill executed",
                    fill_qty=fill_quantity,
                    fill_price=fill_price,
                )
            else:
                fsm.transition(
                    target_state=OrderState.PARTIALLY_FILLED,
                    event=OrderEvent.PARTIAL_FILL,
                    reason="Partial fill executed",
                    fill_qty=fill_quantity,
                    fill_price=fill_price,
                )

            return fsm.snapshot()

    def cancel_order(self, client_order_id: str) -> Order:
        """Cancel an open order on both broker (if PAPER) and FSM."""
        with self._lock:
            fsm = self._state_machines.get(client_order_id)
            if fsm is None:
                raise OrderValidationError(f"Order '{client_order_id}' not found in state machine")

            if self._mode == PaperShadowMode.PAPER and self._guarded_broker is not None:
                self._guarded_broker.cancel_order(client_order_id)

            fsm.transition(
                target_state=OrderState.CANCELLED,
                event=OrderEvent.CANCEL,
                reason="Cancelled by Router",
            )
            return fsm.snapshot()

    def get_order(self, client_order_id: str) -> Order | None:
        """Retrieve latest Order record from state machine snapshot."""
        with self._lock:
            fsm = self._state_machines.get(client_order_id)
            return fsm.snapshot() if fsm is not None else None

    def get_all_orders(self) -> tuple[Order, ...]:
        """Return defensive snapshot of all orders."""
        with self._lock:
            return tuple(fsm.snapshot() for fsm in self._state_machines.values())

    def verify_order_quantity_conservation(self, order: Order) -> bool:
        """
        Verify state-aware order quantity conservation invariant.
        Formula: quantity == filled_quantity + remaining_quantity (for active/partial states)
        or terminal conservation accounting for cancelled/rejected quantities.
        """
        remaining = order.quantity - order.filled_quantity
        if order.state == OrderState.FILLED:
            return order.filled_quantity == order.quantity
        if order.state == OrderState.PARTIALLY_FILLED:
            return 0 < order.filled_quantity < order.quantity and remaining > 0
        if order.state in (
            OrderState.CREATED,
            OrderState.VALIDATED,
            OrderState.SUBMITTED,
            OrderState.ACKNOWLEDGED,
        ):
            return order.filled_quantity == 0 and remaining == order.quantity
        if order.state in (OrderState.CANCELLED, OrderState.REJECTED):
            return order.filled_quantity <= order.quantity
        return True
