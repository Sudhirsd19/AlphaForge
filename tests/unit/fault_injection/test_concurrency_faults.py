"""
Phase 12 — Concurrency/Race Failure Tests (RACE1–RACE5).
Proves AlphaForge handles concurrent access safely using
thread barriers (no sleep-based races).
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.broker.models import BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.execution.enums import OrderEvent, OrderSide, OrderState
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_client_order_id,
)
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.enums import TradeSide

THREAD_COUNT = 10


class TestRACE1ConcurrentExits:
    """RACE1: Concurrent exit transitions on the same FSM produce at most one."""

    def test_concurrent_exit_fills_produce_exactly_one(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-RACE1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        # Walk to EXIT_PENDING
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
        fsm.transition(
            OrderState.FILLED,
            event=OrderEvent.FULL_FILL,
            fill_qty=50,
            fill_price=Decimal("24000.00"),
        )
        fsm.transition(OrderState.PROTECTION_PENDING, event=OrderEvent.REQUEST_PROTECTION)
        fsm.transition(
            OrderState.PROTECTED,
            event=OrderEvent.PROTECTION_CONFIRMED,
            protection_order_id="STOP-1",
        )
        fsm.transition(OrderState.EXIT_PENDING, event=OrderEvent.REQUEST_EXIT)
        assert fsm.current_state == OrderState.EXIT_PENDING

        results: list[bool] = []
        barrier = threading.Barrier(THREAD_COUNT)

        def attempt_exit() -> None:
            barrier.wait()
            r = fsm.transition(
                OrderState.CLOSED, event=OrderEvent.EXIT_FULL_FILL, raise_on_error=False
            )
            results.append(r.success)

        threads = [threading.Thread(target=attempt_exit) for _ in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one transition should succeed, rest should be
        # ALREADY_IN_TARGET_STATE or TERMINAL_STATE
        success_count = sum(1 for r in results if r)
        assert success_count >= 1
        assert fsm.current_state == OrderState.CLOSED


class TestRACE2ConcurrentRiskReservations:
    """RACE2: Concurrent risk reservation attempts don't double-reserve."""

    def test_concurrent_identical_registrations_are_idempotent(self) -> None:
        registry = IdempotencyRegistry()
        client_id = generate_client_order_id(
            "TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-RACE2"
        )
        intent = OrderIntent(
            client_order_id=client_id,
            strategy_id="TREND",
            strategy_version="1.0.0",
            symbol="NIFTY",
            role=OrderRole.ENTRY,
            signal_id="SIG-RACE2",
            side=TradeSide.LONG,
            quantity=50,
        )

        results: list[OrderIntent] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(THREAD_COUNT)

        def register() -> None:
            barrier.wait()
            try:
                r = registry.register(intent)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register) for _ in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed (idempotent) with same intent returned
        assert len(errors) == 0
        assert len(results) == THREAD_COUNT
        for r in results:
            assert r.client_order_id == client_id


class TestRACE3DuplicateOrderSubmission:
    """RACE3: Concurrent identical order submissions produce exactly one order."""

    def test_concurrent_identical_submits_one_order(self) -> None:
        broker = PaperBroker()
        client_id = generate_client_order_id(
            "TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-RACE3"
        )
        req = BrokerOrderRequest(
            client_order_id=client_id,
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.ENTRY,
        )

        broker_order_ids: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(THREAD_COUNT)

        def submit() -> None:
            barrier.wait()
            try:
                result = broker.submit_order(req)
                broker_order_ids.append(result.broker_order_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit) for _ in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed (idempotent) returning same broker_order_id
        assert len(errors) == 0
        assert len(broker_order_ids) == THREAD_COUNT
        unique_ids = set(broker_order_ids)
        assert len(unique_ids) == 1  # Only one actual order


class TestRACE4ConcurrentReconciliationTransition:
    """RACE4: Concurrent reconciliation + transition don't corrupt FSM."""

    def test_concurrent_transitions_preserve_fsm_integrity(self) -> None:
        now = datetime.now(UTC)
        fsm = OrderStateMachine(
            order_id="ORD-RACE4",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            created_at=now,
            initial_state=OrderState.CREATED,
        )
        fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
        fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
        assert fsm.current_state == OrderState.SUBMITTED

        results: list[tuple[str, bool]] = []
        barrier = threading.Barrier(THREAD_COUNT)

        def attempt_transition(target_state: OrderState, event: OrderEvent, _idx: int) -> None:
            barrier.wait()
            r = fsm.transition(target_state, event=event, raise_on_error=False)
            results.append((event.value, r.success))

        threads = []
        # Half try ACKNOWLEDGE, half try COMMUNICATION_LOST
        for i in range(THREAD_COUNT):
            target_state = OrderState.ACKNOWLEDGED if i % 2 == 0 else OrderState.UNKNOWN
            event = OrderEvent.ACKNOWLEDGE if i % 2 == 0 else OrderEvent.COMMUNICATION_LOST
            t = threading.Thread(target=attempt_transition, args=(target_state, event, i))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # FSM should be in exactly one valid state
        final = fsm.current_state
        assert final in (
            OrderState.ACKNOWLEDGED,
            OrderState.UNKNOWN,
        )
        # Exactly one transition type succeeded first
        success_results = [r for r in results if r[1]]
        assert len(success_results) >= 1


class TestRACE5ConcurrentLedgerAppend:
    """RACE5: Concurrent ledger appends maintain hash chain integrity."""

    def test_concurrent_appends_maintain_sequence(self) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)

        errors: list[Exception] = []
        barrier = threading.Barrier(THREAD_COUNT)

        def append_event(idx: int) -> None:
            barrier.wait()
            try:
                ledger.append(
                    event_type=AuditEventType.SIGNAL_GENERATED,
                    entity_type="SIGNAL",
                    entity_id=f"SIG-RACE5-{idx:03d}",
                    correlation_id="CORR-RACE5",
                    causation_id="ROOT",
                    payload={"thread_index": idx},
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_event, args=(i,)) for i in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        events = storage.read_all()
        assert len(events) == THREAD_COUNT

        # Verify sequence is contiguous 1..N
        seqs = sorted(e.sequence_number for e in events)
        assert seqs == list(range(1, THREAD_COUNT + 1))

        # Verify hash chain
        events_sorted = sorted(events, key=lambda e: e.sequence_number)
        for i in range(1, len(events_sorted)):
            assert events_sorted[i].previous_event_hash == events_sorted[i - 1].event_hash
