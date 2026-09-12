"""
AlphaForge 9-Step Cold-Boot Reconciler.
Implements authoritative state alignment between local persisted state and broker reality.
Guarantees fail-closed entry blocking, protection re-verification, and deterministic
FSM state restoration.
"""

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
)
from alphaforge.execution.enums import OrderSide, OrderState
from alphaforge.execution.idempotency import OrderRole
from alphaforge.execution.protection_watchdog import ProtectionWatchdog
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    OrderReconciliationRecord,
    PositionReconciliationRecord,
    ReconciliationAction,
    ReconciliationObserver,
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)
from alphaforge.reconciliation.state_store import (
    AtomicStateStore,
    InMemoryStateStore,
)
from alphaforge.risk.enums import TradeSide


class ColdBootReconciler:
    """
    Authoritative 9-Step Cold-Boot & Runtime Reconciler for AlphaForge.
    Ensures local order and position records reflect exchange/broker facts before
    allowing new order submissions.
    """

    def __init__(
        self,
        broker: AbstractBroker,
        state_store: AtomicStateStore | InMemoryStateStore,
        gate: ReconciliationGate,
        watchdog: ProtectionWatchdog | None = None,
        reconciliation_listener: Callable[[ReconciliationResult], None]
        | ReconciliationObserver
        | None = None,
        on_start_listener: Callable[[str], None] | None = None,
    ) -> None:
        self._broker = broker
        self._state_store = state_store
        self._gate = gate
        self._watchdog = watchdog if watchdog is not None else ProtectionWatchdog()
        self._reconciliation_listener = reconciliation_listener
        self._on_start_listener = on_start_listener
        self._last_audit_error: Exception | None = None
        self._last_result: ReconciliationResult | None = None
        self._lock = threading.RLock()
        self._pass_counter: int = 0

    @property
    def broker(self) -> AbstractBroker:
        return self._broker

    @property
    def state_store(self) -> AtomicStateStore | InMemoryStateStore:
        return self._state_store

    @property
    def gate(self) -> ReconciliationGate:
        return self._gate

    @property
    def last_audit_error(self) -> Exception | None:
        return self._last_audit_error

    @property
    def last_result(self) -> ReconciliationResult | None:
        return self._last_result

    def _notify_start(self, recon_id: str) -> None:
        if self._on_start_listener is not None:
            self._on_start_listener(recon_id)
        elif self._reconciliation_listener is not None and hasattr(
            self._reconciliation_listener, "on_reconciliation_start"
        ):
            self._reconciliation_listener.on_reconciliation_start(recon_id)

    def _notify_result(self, result: ReconciliationResult) -> None:
        if self._reconciliation_listener is not None:
            if hasattr(self._reconciliation_listener, "on_reconciliation_result"):
                self._reconciliation_listener.on_reconciliation_result(result)
            elif callable(self._reconciliation_listener):
                self._reconciliation_listener(result)

    @staticmethod
    def _is_valid_resting_stop_for_position(
        order: BrokerOrder,
        pos_symbol: str,
        pos_side: TradeSide,
        pos_quantity: int,
        protection_order_id: str | None = None,
    ) -> bool:
        """
        Authoritatively determine if a broker order is a valid resting protective stop
        for an active position using strict matching hierarchy.
        """
        if pos_quantity <= 0:
            return False

        # 1. Correct symbol
        if order.symbol != pos_symbol:
            return False

        # 2. Strict resting active status: only ACKNOWLEDGED is a valid resting stop
        if order.status != BrokerOrderStatus.ACKNOWLEDGED:
            return False

        # 3. Mandatory: BOTH role == OrderRole.STOP AND order_type == BrokerOrderType.STOP_LOSS
        if order.role != OrderRole.STOP or order.order_type != BrokerOrderType.STOP_LOSS:
            return False

        # 4. Strict directional correctness: LONG exits via SELL, SHORT exits via BUY
        expected_side = OrderSide.SELL if pos_side == TradeSide.LONG else OrderSide.BUY
        if order.side != expected_side:
            return False

        # 5. Exact quantity coverage (stop quantity must strictly equal active position quantity)
        if order.quantity != pos_quantity:
            return False

        # 6. Explicit protection identity match if known locally
        if protection_order_id is not None:
            clean_prot_id = protection_order_id.strip().upper()
            if order.client_order_id != clean_prot_id and order.broker_order_id != clean_prot_id:
                return False

        return True

    def _find_confirmed_resting_stop(
        self,
        pos_symbol: str,
        pos_side: TradeSide,
        pos_quantity: int,
        protection_order_id: str | None,
        broker_open_orders: Sequence[BrokerOrder],
    ) -> BrokerOrder | None:
        """
        Find an authoritative broker-side resting stop order protecting the active position.

        Authoritative Matching Rules:
        1. If protection_order_id is NOT None:
           - ONLY accept a broker stop whose client_order_id == protection_order_id
             or broker_order_id == protection_order_id.
           - If no exact linked broker stop exists, return None.
           - DO NOT perform generic fallback matching.
        2. If protection_order_id IS None:
           - Generic strict matching may be used.
        """
        if pos_quantity <= 0:
            return None

        # 1. If explicit protection_order_id is specified:
        if protection_order_id is not None:
            for ord in broker_open_orders:
                if self._is_valid_resting_stop_for_position(
                    order=ord,
                    pos_symbol=pos_symbol,
                    pos_side=pos_side,
                    pos_quantity=pos_quantity,
                    protection_order_id=protection_order_id,
                ):
                    return ord
            # MUST NOT perform generic fallback matching
            return None

        # 2. If protection_order_id is None:
        for ord in broker_open_orders:
            if self._is_valid_resting_stop_for_position(
                order=ord,
                pos_symbol=pos_symbol,
                pos_side=pos_side,
                pos_quantity=pos_quantity,
                protection_order_id=None,
            ):
                return ord

        return None

    def reconcile(self, current_time: datetime | None = None) -> ReconciliationResult:
        """
        Execute the authoritative 9-step reconciliation flow.

        Sequence:
        1. Load local open orders from persistence.
        2. Load local open positions from persistence.
        3. Query broker open orders.
        4. Query broker positions.
        5. Compare local vs broker state (Cases A, B, C, D, E, F).
        6. Resolve deterministic matches.
        7. Keep new entries BLOCKED.
        8. Verify protection for active positions.
        9. Resume only after reconciliation success.
        """
        with self._lock:
            self._pass_counter += 1
            now = current_time if current_time is not None else datetime.now(UTC)
            recon_id = f"RECON-{int(now.timestamp())}-{self._pass_counter:04d}"

            audit_exc: Exception | None = None
            self._last_audit_error = None
            try:
                self._notify_start(recon_id)
            except Exception as exc:
                self._last_audit_error = exc
                audit_exc = exc

            # Step 7 (Pre-emptive): Keep new entries strictly BLOCKED during reconciliation
            self._gate.close("Reconciliation in progress")

            # Step 1 & 2: Load local open orders and positions
            snapshot = self._state_store.load_snapshot()
            local_orders = snapshot.orders if snapshot else {}
            local_positions = snapshot.positions if snapshot else {}

            # Step 3 & 4: Query broker open orders and positions
            if not self._broker.is_available():
                result = ReconciliationResult(
                    reconciliation_id=recon_id,
                    timestamp=now,
                    status=ReconciliationStatus.FAILED,
                    local_order_count=len(local_orders),
                    broker_order_count=0,
                    local_position_count=len(local_positions),
                    broker_position_count=0,
                    matched_count=0,
                    mismatch_count=0,
                    unknown_count=0,
                    new_entries_allowed=False,
                    manual_escalation_required=True,
                    reason_code=ReconciliationReasonCode.BROKER_UNAVAILABLE,
                    order_details=(),
                    position_details=(),
                )
                self._gate.close("Reconciliation failed: Broker unavailable")
                self._last_result = result
                try:
                    self._notify_result(result)
                except Exception as exc:
                    self._last_audit_error = exc
                    if audit_exc is None:
                        audit_exc = exc
                if audit_exc is not None:
                    raise audit_exc
                return result

            broker_open_orders = self._broker.get_open_orders()
            broker_positions = self._broker.get_positions()

            broker_orders_by_client_id = {
                order.client_order_id: order for order in broker_open_orders
            }
            broker_positions_by_symbol = {pos.symbol: pos for pos in broker_positions}

            # Step 5 & 6: Compare local vs broker state & resolve matches
            matched_count = 0
            mismatch_count = 0
            unknown_count = 0

            order_records: list[OrderReconciliationRecord] = []
            position_records: list[PositionReconciliationRecord] = []

            # --- Compare Orders ---
            for client_id, local_ord in local_orders.items():
                if local_ord.state in (
                    OrderState.CLOSED,
                    OrderState.REJECTED,
                    OrderState.CANCELLED,
                    OrderState.MANUAL_ESCALATION,
                ):
                    # Already terminal locally
                    order_records.append(
                        OrderReconciliationRecord(
                            client_order_id=client_id,
                            local_state=local_ord.state,
                            action=ReconciliationAction.NONE,
                            reason_code=ReconciliationReasonCode.MATCHED,
                            notes=f"Order is already in terminal state '{local_ord.state}'",
                        )
                    )
                    continue

                if client_id in broker_orders_by_client_id:
                    # Case A: Local order exists + broker order exists
                    brk_ord = broker_orders_by_client_id[client_id]
                    expected_side = (
                        OrderSide.BUY if local_ord.side == TradeSide.LONG else OrderSide.SELL
                    )
                    if (
                        brk_ord.symbol != local_ord.symbol
                        or brk_ord.side != expected_side
                        or brk_ord.quantity != local_ord.quantity
                    ):
                        mismatch_count += 1
                        order_records.append(
                            OrderReconciliationRecord(
                                client_order_id=client_id,
                                broker_order_id=brk_ord.broker_order_id,
                                local_state=local_ord.state,
                                broker_status=brk_ord.status,
                                local_quantity=local_ord.quantity,
                                broker_filled_quantity=brk_ord.filled_quantity,
                                action=ReconciliationAction.ESCALATE_MANUAL,
                                reason_code=ReconciliationReasonCode.INVALID_BROKER_STATE,
                                notes="Conflicting order attributes between local and broker",
                            )
                        )
                    else:
                        if brk_ord.status == BrokerOrderStatus.ACKNOWLEDGED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=brk_ord.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=brk_ord.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=brk_ord.filled_quantity,
                                    action=ReconciliationAction.SYNC_ACKNOWLEDGED,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Matched open resting order",
                                )
                            )
                        elif brk_ord.status == BrokerOrderStatus.PARTIALLY_FILLED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=brk_ord.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=brk_ord.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=brk_ord.filled_quantity,
                                    action=ReconciliationAction.SYNC_PARTIAL_FILL,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes=(
                                        f"Partially filled: "
                                        f"{brk_ord.filled_quantity}/{local_ord.quantity}"
                                    ),
                                )
                            )
                        elif brk_ord.status == BrokerOrderStatus.FILLED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=brk_ord.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=brk_ord.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=brk_ord.filled_quantity,
                                    action=ReconciliationAction.SYNC_FULL_FILL,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Order 100% filled at broker",
                                )
                            )
                        elif brk_ord.status == BrokerOrderStatus.CANCELLED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=brk_ord.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=brk_ord.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=brk_ord.filled_quantity,
                                    action=ReconciliationAction.SYNC_CANCELLED,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Order cancelled at broker",
                                )
                            )
                else:
                    # Case B: Local order exists + broker order missing from open orders
                    individual_query = self._broker.get_order(client_order_id=client_id)
                    if individual_query is not None:
                        if individual_query.status == BrokerOrderStatus.FILLED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=individual_query.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=individual_query.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=individual_query.filled_quantity,
                                    action=ReconciliationAction.SYNC_FULL_FILL,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Order verified filled in broker order history",
                                )
                            )
                        elif individual_query.status == BrokerOrderStatus.CANCELLED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=individual_query.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=individual_query.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=individual_query.filled_quantity,
                                    action=ReconciliationAction.SYNC_CANCELLED,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Order verified cancelled in broker order history",
                                )
                            )
                        elif individual_query.status == BrokerOrderStatus.REJECTED:
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=individual_query.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=individual_query.status,
                                    local_quantity=local_ord.quantity,
                                    broker_filled_quantity=individual_query.filled_quantity,
                                    action=ReconciliationAction.SYNC_CANCELLED,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Order rejected at broker",
                                )
                            )
                        else:
                            mismatch_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    broker_order_id=individual_query.broker_order_id,
                                    local_state=local_ord.state,
                                    broker_status=individual_query.status,
                                    action=ReconciliationAction.ESCALATE_MANUAL,
                                    reason_code=ReconciliationReasonCode.BROKER_ORDER_MISSING,
                                    notes="Broker order status ambiguous",
                                )
                            )
                    else:
                        # Order missing entirely from broker
                        if local_ord.state in (OrderState.CREATED, OrderState.VALIDATED):
                            matched_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    local_state=local_ord.state,
                                    action=ReconciliationAction.NONE,
                                    reason_code=ReconciliationReasonCode.MATCHED,
                                    notes="Unsubmitted local order not yet sent to broker",
                                )
                            )
                        else:
                            unknown_count += 1
                            order_records.append(
                                OrderReconciliationRecord(
                                    client_order_id=client_id,
                                    local_state=local_ord.state,
                                    action=ReconciliationAction.ESCALATE_MANUAL,
                                    reason_code=ReconciliationReasonCode.BROKER_ORDER_MISSING,
                                    notes="Order submitted locally but missing on broker",
                                )
                            )

            # Check for broker orders not known locally (Case C)
            for client_id, brk_ord in broker_orders_by_client_id.items():
                if client_id not in local_orders:
                    # Check if this broker order is a confirmed resting stop
                    # protecting an active position
                    is_known_stop = any(
                        self._is_valid_resting_stop_for_position(
                            order=brk_ord,
                            pos_symbol=pos.symbol,
                            pos_side=pos.side,
                            pos_quantity=pos.quantity,
                            protection_order_id=pos.protection_order_id,
                        )
                        for pos in local_positions.values()
                    )

                    if is_known_stop:
                        order_records.append(
                            OrderReconciliationRecord(
                                client_order_id=client_id,
                                broker_order_id=brk_ord.broker_order_id,
                                broker_status=brk_ord.status,
                                broker_filled_quantity=brk_ord.filled_quantity,
                                action=ReconciliationAction.NONE,
                                reason_code=ReconciliationReasonCode.MATCHED,
                                notes="Resting stop order protecting active position",
                            )
                        )
                        continue

                    unknown_count += 1
                    order_records.append(
                        OrderReconciliationRecord(
                            client_order_id=client_id,
                            broker_order_id=brk_ord.broker_order_id,
                            broker_status=brk_ord.status,
                            broker_filled_quantity=brk_ord.filled_quantity,
                            action=ReconciliationAction.ESCALATE_MANUAL,
                            reason_code=ReconciliationReasonCode.UNKNOWN_EXTERNAL_ORDER,
                            notes=(
                                "Broker order has no corresponding local record "
                                "and is not a valid resting stop"
                            ),
                        )
                    )

            # --- Compare Positions ---
            for sym, local_pos in local_positions.items():
                if local_pos.quantity == 0:
                    # Flat position locally
                    if (
                        sym in broker_positions_by_symbol
                        and broker_positions_by_symbol[sym].quantity > 0
                    ):
                        mismatch_count += 1
                        position_records.append(
                            PositionReconciliationRecord(
                                position_id=local_pos.position_id,
                                symbol=sym,
                                local_quantity=0,
                                broker_quantity=broker_positions_by_symbol[sym].quantity,
                                action=ReconciliationAction.ESCALATE_MANUAL,
                                reason_code=ReconciliationReasonCode.POSITION_MISMATCH,
                                notes="Local position is flat but broker reports open exposure",
                            )
                        )
                    else:
                        matched_count += 1
                        position_records.append(
                            PositionReconciliationRecord(
                                position_id=local_pos.position_id,
                                symbol=sym,
                                local_quantity=0,
                                broker_quantity=0,
                                action=ReconciliationAction.NONE,
                                reason_code=ReconciliationReasonCode.POSITION_MATCHED,
                                notes="Position verified flat both locally and at broker",
                            )
                        )
                else:
                    # Active position locally
                    if sym in broker_positions_by_symbol:
                        brk_pos = broker_positions_by_symbol[sym]
                        if (
                            brk_pos.quantity == local_pos.quantity
                            and brk_pos.side == local_pos.side
                        ):
                            # Case D: Matching position
                            confirmed_stop = self._find_confirmed_resting_stop(
                                pos_symbol=sym,
                                pos_side=brk_pos.side,
                                pos_quantity=brk_pos.quantity,
                                protection_order_id=local_pos.protection_order_id,
                                broker_open_orders=broker_open_orders,
                            )
                            is_protected = confirmed_stop is not None

                            matched_count += 1
                            position_records.append(
                                PositionReconciliationRecord(
                                    position_id=local_pos.position_id,
                                    symbol=sym,
                                    local_quantity=local_pos.quantity,
                                    broker_quantity=brk_pos.quantity,
                                    action=ReconciliationAction.NONE,
                                    reason_code=ReconciliationReasonCode.POSITION_MATCHED,
                                    is_protection_confirmed=is_protected,
                                    notes=(
                                        "Position quantities and directions match exactly. "
                                        f"Protection confirmed: {is_protected}"
                                    ),
                                )
                            )
                        else:
                            # Case E: Position mismatch
                            mismatch_count += 1
                            confirmed_stop = self._find_confirmed_resting_stop(
                                pos_symbol=sym,
                                pos_side=brk_pos.side,
                                pos_quantity=brk_pos.quantity,
                                protection_order_id=local_pos.protection_order_id,
                                broker_open_orders=broker_open_orders,
                            )
                            position_records.append(
                                PositionReconciliationRecord(
                                    position_id=local_pos.position_id,
                                    symbol=sym,
                                    local_quantity=local_pos.quantity,
                                    broker_quantity=brk_pos.quantity,
                                    action=ReconciliationAction.ESCALATE_MANUAL,
                                    reason_code=ReconciliationReasonCode.POSITION_MISMATCH,
                                    is_protection_confirmed=confirmed_stop is not None,
                                    notes=(
                                        f"Position mismatch: Local "
                                        f"({local_pos.quantity}, {local_pos.side}) != "
                                        f"Broker ({brk_pos.quantity}, {brk_pos.side})"
                                    ),
                                )
                            )
                    else:
                        # Local position has exposure but broker position is missing
                        mismatch_count += 1
                        position_records.append(
                            PositionReconciliationRecord(
                                position_id=local_pos.position_id,
                                symbol=sym,
                                local_quantity=local_pos.quantity,
                                broker_quantity=0,
                                action=ReconciliationAction.ESCALATE_MANUAL,
                                reason_code=ReconciliationReasonCode.POSITION_MISMATCH,
                                is_protection_confirmed=False,
                                notes="Local position open but broker reports no position",
                            )
                        )

            # Check for broker positions not known locally (Case F)
            for sym, brk_pos in broker_positions_by_symbol.items():
                if brk_pos.quantity > 0 and sym not in local_positions:
                    unknown_count += 1
                    confirmed_stop = self._find_confirmed_resting_stop(
                        pos_symbol=sym,
                        pos_side=brk_pos.side,
                        pos_quantity=brk_pos.quantity,
                        protection_order_id=None,
                        broker_open_orders=broker_open_orders,
                    )
                    position_records.append(
                        PositionReconciliationRecord(
                            position_id=brk_pos.position_id,
                            symbol=sym,
                            local_quantity=0,
                            broker_quantity=brk_pos.quantity,
                            action=ReconciliationAction.ESCALATE_MANUAL,
                            reason_code=ReconciliationReasonCode.UNKNOWN_EXTERNAL_POSITION,
                            is_protection_confirmed=confirmed_stop is not None,
                            notes="Broker position exists without any local record",
                        )
                    )

            # Step 8: Verify protection for active positions
            protection_hazard = False
            for pos_rec in position_records:
                active_qty = max(pos_rec.local_quantity, pos_rec.broker_quantity)
                if active_qty > 0 and not pos_rec.is_protection_confirmed:
                    protection_hazard = True
                    mismatch_count += 1
                    # Flag protection hazard
                    order_records.append(
                        OrderReconciliationRecord(
                            client_order_id=f"PROT-HAZARD-{pos_rec.symbol}",
                            action=ReconciliationAction.TRIGGER_EMERGENCY_PROTECTION,
                            reason_code=ReconciliationReasonCode.PROTECTION_UNCONFIRMED,
                            notes=(
                                f"Active position in {pos_rec.symbol} "
                                "lacks confirmed resting stop protection on broker"
                            ),
                        )
                    )

            # Step 9: Resume only after successful reconciliation
            is_matched = mismatch_count == 0 and unknown_count == 0 and not protection_hazard

            if is_matched:
                status = ReconciliationStatus.MATCHED
                reason_code = ReconciliationReasonCode.MATCHED
                new_entries_allowed = True
                manual_escalation = False
            else:
                new_entries_allowed = False
                manual_escalation = True
                if any(
                    p.reason_code == ReconciliationReasonCode.UNKNOWN_EXTERNAL_POSITION
                    for p in position_records
                ):
                    status = ReconciliationStatus.ESCALATED
                    reason_code = ReconciliationReasonCode.UNKNOWN_EXTERNAL_POSITION
                elif any(
                    o.reason_code == ReconciliationReasonCode.UNKNOWN_EXTERNAL_ORDER
                    for o in order_records
                ):
                    status = ReconciliationStatus.ESCALATED
                    reason_code = ReconciliationReasonCode.UNKNOWN_EXTERNAL_ORDER
                elif any(
                    p.reason_code == ReconciliationReasonCode.POSITION_MISMATCH
                    for p in position_records
                ):
                    status = ReconciliationStatus.MISMATCH
                    reason_code = ReconciliationReasonCode.POSITION_MISMATCH
                elif protection_hazard:
                    status = ReconciliationStatus.MISMATCH
                    reason_code = ReconciliationReasonCode.PROTECTION_UNCONFIRMED
                elif any(
                    o.reason_code == ReconciliationReasonCode.BROKER_ORDER_MISSING
                    for o in order_records
                ):
                    status = ReconciliationStatus.ESCALATED
                    reason_code = ReconciliationReasonCode.BROKER_ORDER_MISSING
                else:
                    status = ReconciliationStatus.MISMATCH
                    reason_code = ReconciliationReasonCode.INVALID_LOCAL_STATE

            result = ReconciliationResult(
                reconciliation_id=recon_id,
                timestamp=now,
                status=status,
                local_order_count=len(local_orders),
                broker_order_count=len(broker_orders_by_client_id),
                local_position_count=len(local_positions),
                broker_position_count=len(broker_positions_by_symbol),
                matched_count=matched_count,
                mismatch_count=mismatch_count,
                unknown_count=unknown_count,
                new_entries_allowed=new_entries_allowed,
                manual_escalation_required=manual_escalation,
                reason_code=reason_code,
                order_details=tuple(order_records),
                position_details=tuple(position_records),
            )

            self._last_result = result

            # Open gate ONLY if 100% matched and safe
            if new_entries_allowed:
                self._gate.open(result)
            else:
                self._gate.close(f"Reconciliation {status.value}: {reason_code.value}")

            try:
                self._notify_result(result)
            except Exception as exc:
                self._last_audit_error = exc
                if audit_exc is None:
                    audit_exc = exc

            if audit_exc is not None:
                raise audit_exc

            return result

    def align_fsm(
        self,
        fsm: OrderStateMachine,
        record: OrderReconciliationRecord,
    ) -> None:
        """
        Synchronize a Phase 7 OrderStateMachine instance using authoritative reconciliation facts.
        Transitions the FSM cleanly through UNKNOWN -> RECONCILING -> target state.
        """
        if record.action == ReconciliationAction.NONE:
            return

        # Ensure order is in RECONCILING
        if fsm.current_state == OrderState.UNKNOWN:
            fsm.transition(OrderState.RECONCILING)
        elif fsm.current_state != OrderState.RECONCILING:
            # If not in UNKNOWN/RECONCILING, transition through UNKNOWN if permitted
            if fsm.can_transition(OrderState.UNKNOWN):
                fsm.transition(OrderState.UNKNOWN)
                fsm.transition(OrderState.RECONCILING)
            elif fsm.is_terminal:
                return

        if record.action == ReconciliationAction.SYNC_ACKNOWLEDGED:
            fsm.transition(OrderState.ACKNOWLEDGED, reason="Reconciled acknowledged at broker")
        elif record.action == ReconciliationAction.SYNC_PARTIAL_FILL:
            qty = record.broker_filled_quantity or 0
            fsm.transition(OrderState.PARTIALLY_FILLED, fill_qty=qty)
        elif record.action == ReconciliationAction.SYNC_FULL_FILL:
            fsm.transition(OrderState.FILLED, reason="Reconciled full fill at broker")
        elif record.action == ReconciliationAction.SYNC_CANCELLED:
            fsm.transition(OrderState.CANCELLED, reason="Reconciled cancelled at broker")
        elif record.action == ReconciliationAction.SYNC_CLOSED:
            fsm.transition(OrderState.CLOSED, reason="Reconciled closed at broker")
        elif record.action in (
            ReconciliationAction.ESCALATE_MANUAL,
            ReconciliationAction.TRIGGER_EMERGENCY_PROTECTION,
        ):
            fsm.transition(
                OrderState.MANUAL_ESCALATION, reason=f"Reconciliation escalation: {record.notes}"
            )
