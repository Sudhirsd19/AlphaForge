"""
AlphaForge Simulated Paper Broker.
Provides a deterministic, thread-safe in-memory broker implementation with
idempotent submission handling, position lifecycle simulation, and failure injection.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
)
from alphaforge.core.exceptions import (
    BrokerError,
    BrokerOrderCollisionError,
    BrokerUnavailableError,
)
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.risk.enums import TradeSide


class PaperBroker(AbstractBroker):
    """
    Deterministic Simulated Paper Broker for Phase 8 testing and validation.
    Maintains broker-side orders and positions in memory with complete thread-safety.
    Never connects to external networks or live trading venues.
    """

    def __init__(self) -> None:
        self._orders_by_client_id: dict[str, BrokerOrder] = {}
        self._orders_by_broker_id: dict[str, BrokerOrder] = {}
        self._positions: dict[str, BrokerPosition] = {}
        self._is_available: bool = True
        self._simulate_timeout_on_submit: bool = False
        self._order_counter: int = 0
        self._lock: threading.RLock = threading.RLock()

    def is_available(self) -> bool:
        """Check whether paper broker is available."""
        with self._lock:
            return self._is_available

    def set_available(self, available: bool) -> None:
        """Control simulated broker availability."""
        with self._lock:
            self._is_available = available

    def set_simulate_timeout(self, enabled: bool) -> None:
        """Control simulated network timeout on submit_order."""
        with self._lock:
            self._simulate_timeout_on_submit = enabled

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """
        Submit order to paper broker.
        Enforces duplicate idempotency on client_order_id.
        Simulates post-acceptance network timeout if configured.
        """
        with self._lock:
            if not self._is_available:
                raise BrokerUnavailableError("Paper broker is currently marked unavailable")

            client_id = request.client_order_id.strip().upper()

            # 1. Idempotency duplicate check
            existing = self._orders_by_client_id.get(client_id)
            if existing is not None:
                if (
                    existing.symbol == request.symbol.strip().upper()
                    and existing.side == request.side
                    and existing.quantity == request.quantity
                    and existing.role == request.role
                ):
                    return existing
                raise BrokerOrderCollisionError(
                    f"Duplicate client_order_id '{client_id}' submitted with conflicting intent: "
                    f"Existing ({existing.symbol}, {existing.side}, {existing.quantity}, "
                    f"{existing.role}) != New ({request.symbol}, {request.side}, "
                    f"{request.quantity}, {request.role})"
                )

            # 2. Assign unique deterministic broker order ID
            self._order_counter += 1
            broker_order_id = f"BRK-{self._order_counter:06d}"
            now = datetime.now(UTC)

            order = BrokerOrder(
                broker_order_id=broker_order_id,
                client_order_id=client_id,
                symbol=request.symbol.strip().upper(),
                side=request.side,
                quantity=request.quantity,
                role=request.role,
                order_type=request.order_type,
                status=BrokerOrderStatus.ACKNOWLEDGED,
                filled_quantity=0,
                average_price=None,
                created_at=now,
                updated_at=now,
            )

            # Store in internal state
            self._orders_by_client_id[client_id] = order
            self._orders_by_broker_id[broker_order_id] = order

            # 3. Critical Timeout Scenario: Broker accepted and stored order, but network timed out
            if self._simulate_timeout_on_submit:
                msg = (
                    f"Simulated network timeout: Order '{client_id}' accepted "
                    "by broker but response timed out"
                )
                raise TimeoutError(msg)

            return order

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        """Query order by client_order_id or broker_order_id."""
        with self._lock:
            if not self._is_available:
                raise BrokerUnavailableError("Paper broker is currently marked unavailable")
            if client_order_id:
                return self._orders_by_client_id.get(client_order_id.strip().upper())
            if broker_order_id:
                return self._orders_by_broker_id.get(broker_order_id.strip().upper())
            return None

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        """Query all active non-terminal orders."""
        with self._lock:
            if not self._is_available:
                raise BrokerUnavailableError("Paper broker is currently marked unavailable")
            open_statuses = {
                BrokerOrderStatus.PENDING,
                BrokerOrderStatus.ACKNOWLEDGED,
                BrokerOrderStatus.PARTIALLY_FILLED,
            }
            return tuple(
                order
                for order in self._orders_by_client_id.values()
                if order.status in open_statuses
            )

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        """Query all broker positions."""
        with self._lock:
            if not self._is_available:
                raise BrokerUnavailableError("Paper broker is currently marked unavailable")
            return tuple(self._positions.values())

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        """Cancel an open order."""
        with self._lock:
            if not self._is_available:
                raise BrokerUnavailableError("Paper broker is currently marked unavailable")
            clean_id = client_order_id.strip().upper()
            order = self._orders_by_client_id.get(clean_id)
            if order is None:
                raise BrokerError(f"Order '{clean_id}' not found for cancellation")

            if order.status in (
                BrokerOrderStatus.FILLED,
                BrokerOrderStatus.CANCELLED,
                BrokerOrderStatus.REJECTED,
            ):
                raise BrokerError(
                    f"Order '{clean_id}' cannot be cancelled in terminal status '{order.status}'"
                )

            now = datetime.now(UTC)
            updated_order = BrokerOrder(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                role=order.role,
                order_type=order.order_type,
                status=BrokerOrderStatus.CANCELLED,
                filled_quantity=order.filled_quantity,
                average_price=order.average_price,
                created_at=order.created_at,
                updated_at=now,
            )
            self._orders_by_client_id[clean_id] = updated_order
            self._orders_by_broker_id[order.broker_order_id] = updated_order
            return updated_order

    def simulate_acknowledgement(self, client_order_id: str) -> BrokerOrder:
        """Simulate order transition to ACKNOWLEDGED."""
        with self._lock:
            order = self._get_required_order(client_order_id)
            now = datetime.now(UTC)
            updated = BrokerOrder(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                role=order.role,
                order_type=order.order_type,
                status=BrokerOrderStatus.ACKNOWLEDGED,
                filled_quantity=order.filled_quantity,
                average_price=order.average_price,
                created_at=order.created_at,
                updated_at=now,
            )
            self._store_order(updated)
            return updated

    def simulate_partial_fill(
        self,
        client_order_id: str,
        fill_qty: int,
        fill_price: Decimal,
    ) -> BrokerOrder:
        """Simulate execution fill on an order and update corresponding position."""
        with self._lock:
            order = self._get_required_order(client_order_id)
            if fill_qty <= 0:
                raise BrokerError(f"fill_qty must be positive: {fill_qty}")
            if order.status in (BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
                raise BrokerError(f"Cannot fill cancelled or rejected order '{client_order_id}'")

            new_fill = min(order.quantity, order.filled_quantity + fill_qty)
            new_status = (
                BrokerOrderStatus.FILLED
                if new_fill == order.quantity
                else BrokerOrderStatus.PARTIALLY_FILLED
            )
            now = datetime.now(UTC)

            # Update volume-weighted average price
            if order.average_price is None or order.filled_quantity == 0:
                avg_price = fill_price
            else:
                prev_notional = order.average_price * Decimal(order.filled_quantity)
                curr_notional = fill_price * Decimal(fill_qty)
                avg_price = (prev_notional + curr_notional) / Decimal(new_fill)

            updated_order = BrokerOrder(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                role=order.role,
                order_type=order.order_type,
                status=new_status,
                filled_quantity=new_fill,
                average_price=avg_price,
                created_at=order.created_at,
                updated_at=now,
            )
            self._store_order(updated_order)

            # Update broker position
            self._update_position_from_fill(updated_order, fill_qty, fill_price)
            return updated_order

    def simulate_full_fill(self, client_order_id: str, fill_price: Decimal) -> BrokerOrder:
        """Simulate 100% execution fill."""
        with self._lock:
            order = self._get_required_order(client_order_id)
            remaining = order.quantity - order.filled_quantity
            return self.simulate_partial_fill(client_order_id, remaining, fill_price)

    def simulate_rejection(self, client_order_id: str, reason: str = "") -> BrokerOrder:
        """Simulate order rejection by broker."""
        _ = reason
        with self._lock:
            order = self._get_required_order(client_order_id)
            now = datetime.now(UTC)
            updated = BrokerOrder(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                role=order.role,
                order_type=order.order_type,
                status=BrokerOrderStatus.REJECTED,
                filled_quantity=order.filled_quantity,
                average_price=order.average_price,
                created_at=order.created_at,
                updated_at=now,
            )
            self._store_order(updated)
            return updated

    def inject_external_order(self, order: BrokerOrder) -> None:
        """Inject an order into broker state for testing Case C reconciliation."""
        with self._lock:
            self._store_order(order)

    def inject_external_position(self, position: BrokerPosition) -> None:
        """Inject a position into broker state for testing Case F reconciliation."""
        with self._lock:
            self._positions[position.symbol] = position

    def clear(self) -> None:
        """Reset broker state."""
        with self._lock:
            self._orders_by_client_id.clear()
            self._orders_by_broker_id.clear()
            self._positions.clear()
            self._is_available = True
            self._simulate_timeout_on_submit = False
            self._order_counter = 0

    def _get_required_order(self, client_order_id: str) -> BrokerOrder:
        clean_id = client_order_id.strip().upper()
        order = self._orders_by_client_id.get(clean_id)
        if order is None:
            raise BrokerError(f"Order '{clean_id}' not found in paper broker")
        return order

    def _store_order(self, order: BrokerOrder) -> None:
        self._orders_by_client_id[order.client_order_id] = order
        self._orders_by_broker_id[order.broker_order_id] = order

    def _update_position_from_fill(
        self,
        order: BrokerOrder,
        fill_qty: int,
        fill_price: Decimal,
    ) -> None:
        sym = order.symbol
        existing = self._positions.get(sym)

        if order.role == OrderRole.ENTRY:
            trade_side = TradeSide.LONG if order.side == OrderSide.BUY else TradeSide.SHORT
            if existing is None or existing.quantity == 0:
                pos_id = f"POS-{sym}-{int(datetime.now(UTC).timestamp())}"
                self._positions[sym] = BrokerPosition(
                    position_id=pos_id,
                    symbol=sym,
                    side=trade_side,
                    quantity=fill_qty,
                    average_price=fill_price,
                    status="OPEN",
                )
            else:
                # Add to existing position
                total_qty = existing.quantity + fill_qty
                prev_notional = (existing.average_price or fill_price) * Decimal(existing.quantity)
                curr_notional = fill_price * Decimal(fill_qty)
                avg_p = (prev_notional + curr_notional) / Decimal(total_qty)
                self._positions[sym] = BrokerPosition(
                    position_id=existing.position_id,
                    symbol=sym,
                    side=existing.side,
                    quantity=total_qty,
                    average_price=avg_p,
                    status="OPEN",
                )
        elif order.role in (OrderRole.STOP, OrderRole.EXIT):
            if existing is not None:
                new_qty = max(0, existing.quantity - fill_qty)
                status = "OPEN" if new_qty > 0 else "FLAT"
                self._positions[sym] = BrokerPosition(
                    position_id=existing.position_id,
                    symbol=sym,
                    side=existing.side,
                    quantity=new_qty,
                    average_price=existing.average_price if new_qty > 0 else None,
                    status=status,
                )
