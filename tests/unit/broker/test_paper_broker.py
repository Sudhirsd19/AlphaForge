"""
Unit tests for AlphaForge Simulated Paper Broker.
Verifies submission idempotency, timeout-after-acceptance simulation,
execution fills, position tracking, and failure handling.
"""

import threading
from decimal import Decimal

import pytest

from alphaforge.broker.models import (
    BrokerOrderRequest,
    BrokerOrderStatus,
)
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.exceptions import (
    BrokerError,
    BrokerOrderCollisionError,
    BrokerPositionConflictError,
    BrokerUnavailableError,
)
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole


def test_paper_broker_submission_and_query() -> None:
    """Submit order to paper broker and verify query by client_order_id and broker_order_id."""
    broker = PaperBroker()
    req = BrokerOrderRequest(
        client_order_id="AF-E-SUBMIT1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    order = broker.submit_order(req)
    assert order.client_order_id == "AF-E-SUBMIT1"
    assert order.status == BrokerOrderStatus.ACKNOWLEDGED
    assert order.filled_quantity == 0

    # Query by client_order_id
    q_client = broker.get_order(client_order_id="AF-E-SUBMIT1")
    assert q_client == order

    # Query by broker_order_id
    q_broker = broker.get_order(broker_order_id=order.broker_order_id)
    assert q_broker == order


def test_paper_broker_duplicate_idempotency() -> None:
    """Duplicate submission with matching intent returns the exact same BrokerOrder."""
    broker = PaperBroker()
    req = BrokerOrderRequest(
        client_order_id="AF-E-DUP1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    ord1 = broker.submit_order(req)
    ord2 = broker.submit_order(req)

    assert ord1.broker_order_id == ord2.broker_order_id
    assert len(broker.get_open_orders()) == 1


def test_paper_broker_duplicate_collision_error() -> None:
    """Duplicate submission with conflicting intent raises BrokerOrderCollisionError."""
    broker = PaperBroker()
    req1 = BrokerOrderRequest(
        client_order_id="AF-E-COLLIDE",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req1)

    req_conflict = BrokerOrderRequest(
        client_order_id="AF-E-COLLIDE",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=100,  # Conflict!
        role=OrderRole.ENTRY,
    )
    with pytest.raises(BrokerOrderCollisionError, match="Duplicate client_order_id"):
        broker.submit_order(req_conflict)


def test_paper_broker_timeout_after_acceptance_scenario() -> None:
    """
    Critical Scenario: Broker accepts and stores order, but network response times out.
    Proves that a timeout does NOT mean the order was rejected.
    """
    broker = PaperBroker()
    broker.set_simulate_timeout(True)

    req = BrokerOrderRequest(
        client_order_id="AF-E-TIMEOUT",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )

    # Submission raises TimeoutError
    with pytest.raises(TimeoutError, match="Simulated network timeout"):
        broker.submit_order(req)

    # Disable timeout and verify order actually exists on broker book!
    broker.set_simulate_timeout(False)
    stored = broker.get_order(client_order_id="AF-E-TIMEOUT")
    assert stored is not None
    assert stored.client_order_id == "AF-E-TIMEOUT"
    assert stored.status == BrokerOrderStatus.ACKNOWLEDGED


def test_paper_broker_partial_and_full_fills() -> None:
    """Simulate partial and full execution fills and verify position tracking."""
    broker = PaperBroker()
    req = BrokerOrderRequest(
        client_order_id="AF-E-FILL",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=100,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req)

    # Partial fill 40 @ 24500.00
    part_ord = broker.simulate_partial_fill("AF-E-FILL", 40, Decimal("24500.00"))
    assert part_ord.status == BrokerOrderStatus.PARTIALLY_FILLED
    assert part_ord.filled_quantity == 40
    assert part_ord.average_price == Decimal("24500.00")

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "NIFTY"
    assert positions[0].quantity == 40

    # Full fill remaining 60 @ 24550.00
    full_ord = broker.simulate_full_fill("AF-E-FILL", Decimal("24550.00"))
    assert full_ord.status == BrokerOrderStatus.FILLED
    assert full_ord.filled_quantity == 100

    # Average price = (40*24500 + 60*24550) / 100 = (980000 + 1473000)/100 = 24530.00
    assert full_ord.average_price == Decimal("24530.00")

    pos_after = broker.get_positions()[0]
    assert pos_after.quantity == 100


def test_paper_broker_cancellation() -> None:
    """Cancel open order and reject cancellation on terminal order."""
    broker = PaperBroker()
    req = BrokerOrderRequest(
        client_order_id="AF-E-CANCEL",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req)
    cancelled = broker.cancel_order("AF-E-CANCEL")
    assert cancelled.status == BrokerOrderStatus.CANCELLED

    # Re-cancelling raises BrokerError
    with pytest.raises(BrokerError, match="cannot be cancelled in terminal status"):
        broker.cancel_order("AF-E-CANCEL")


def test_paper_broker_unavailability() -> None:
    """Operations fail closed when broker is marked unavailable."""
    broker = PaperBroker()
    broker.set_available(False)

    req = BrokerOrderRequest(
        client_order_id="AF-E-UNAVAIL",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    with pytest.raises(BrokerUnavailableError):
        broker.submit_order(req)

    with pytest.raises(BrokerUnavailableError):
        broker.get_open_orders()

    with pytest.raises(BrokerUnavailableError):
        broker.get_positions()


def test_paper_broker_rejects_second_entry_same_symbol() -> None:
    """Finding 2 Test A: Reject second ENTRY on existing position (no scale-in)."""
    broker = PaperBroker()
    req1 = BrokerOrderRequest(
        client_order_id="AF-E-POS1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req1)
    broker.simulate_full_fill("AF-E-POS1", Decimal("24500.00"))

    # Verify initial position is 50
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 50

    # Attempt second ENTRY on same symbol
    req2 = BrokerOrderRequest(
        client_order_id="AF-E-POS2",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=25,
        role=OrderRole.ENTRY,
    )
    with pytest.raises(BrokerPositionConflictError, match="Scale-in rejected"):
        broker.submit_order(req2)

    # Position remains strictly 50
    positions_after = broker.get_positions()
    assert len(positions_after) == 1
    assert positions_after[0].quantity == 50


def test_paper_broker_rejects_opposite_side_entry() -> None:
    """Finding 2 Test B: Reject opposite-side ENTRY (no netting/reversal)."""
    broker = PaperBroker()
    req1 = BrokerOrderRequest(
        client_order_id="AF-E-LONG50",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req1)
    broker.simulate_full_fill("AF-E-LONG50", Decimal("24500.00"))

    # Attempt opposite-side ENTRY
    req2 = BrokerOrderRequest(
        client_order_id="AF-E-SHORT25",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=25,
        role=OrderRole.ENTRY,
    )
    with pytest.raises(BrokerPositionConflictError, match="Scale-in rejected"):
        broker.submit_order(req2)

    # Position remains strictly 50
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 50


def test_paper_broker_concurrent_duplicate_entries() -> None:
    """Finding 2 Test C: Concurrent identical submissions produce exactly 1 order and position."""
    broker = PaperBroker()
    req = BrokerOrderRequest(
        client_order_id="AF-E-CONCURRENT",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )

    results = []
    errors = []

    def worker() -> None:
        try:
            ord_res = broker.submit_order(req)
            results.append(ord_res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10
    # All 10 threads received the exact same broker_order_id
    first_id = results[0].broker_order_id
    assert all(r.broker_order_id == first_id for r in results)

    # Fill the single order
    broker.simulate_full_fill("AF-E-CONCURRENT", Decimal("24500.00"))
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 50


def test_paper_broker_valid_exit_reduces_quantity() -> None:
    """Finding 2 Test D: Valid EXIT order reduces active position quantity."""
    broker = PaperBroker()
    req_entry = BrokerOrderRequest(
        client_order_id="AF-E-ENTRY50",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req_entry)
    broker.simulate_full_fill("AF-E-ENTRY50", Decimal("24500.00"))

    # Submit valid EXIT for 20 units
    req_exit = BrokerOrderRequest(
        client_order_id="AF-X-EXIT20",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=20,
        role=OrderRole.EXIT,
    )
    broker.submit_order(req_exit)
    broker.simulate_full_fill("AF-X-EXIT20", Decimal("24600.00"))

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 30
    assert positions[0].status == "OPEN"


def test_paper_broker_valid_stop_reduces_quantity() -> None:
    """Finding 2 Test E: Valid STOP order reduces position to zero / FLAT."""
    broker = PaperBroker()
    req_entry = BrokerOrderRequest(
        client_order_id="AF-E-ENTRY-STOP",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req_entry)
    broker.simulate_full_fill("AF-E-ENTRY-STOP", Decimal("24500.00"))

    # Submit STOP for full 50 units
    req_stop = BrokerOrderRequest(
        client_order_id="AF-S-STOP50",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        role=OrderRole.STOP,
    )
    broker.submit_order(req_stop)
    broker.simulate_full_fill("AF-S-STOP50", Decimal("24400.00"))

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 0
    assert positions[0].status == "FLAT"


def test_paper_broker_exit_cannot_reduce_below_zero() -> None:
    """Finding 2 Test F: Exit quantity exceeding position is rejected."""
    broker = PaperBroker()
    req_entry = BrokerOrderRequest(
        client_order_id="AF-E-ENTRY-EXCESS",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req_entry)
    broker.simulate_full_fill("AF-E-ENTRY-EXCESS", Decimal("24500.00"))

    # Attempt EXIT for 60 units (exceeds 50)
    req_excess_exit = BrokerOrderRequest(
        client_order_id="AF-X-EXCESS60",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=60,
        role=OrderRole.EXIT,
    )
    with pytest.raises(BrokerPositionConflictError, match="exceeds active position quantity"):
        broker.submit_order(req_excess_exit)

    # Position remains 50
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 50
