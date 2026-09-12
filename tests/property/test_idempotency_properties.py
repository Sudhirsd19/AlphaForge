"""
Property-based tests for AlphaForge Idempotency & Crash Recovery using Hypothesis.
Proves mathematical invariants across randomized domains:
1. Determinism: Same canonical intent always produces the exact same client_order_id.
2. Uniqueness: Different intent fields produce distinct client_order_ids.
3. Idempotent single-order invariant: Repeated submissions never create multiple broker orders.
4. Collision detection: Conflicting intent on duplicate client_order_id strictly fails closed.
5. Entry blocking: Gate remains closed on any unresolved mismatch or unknown entity.
"""

import contextlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.broker.models import BrokerOrderRequest, BrokerPosition
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.exceptions import (
    BrokerPositionConflictError,
    IdempotencyCollisionError,
    ReconciliationError,
)
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_client_order_id,
)
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)
from alphaforge.reconciliation.reconciler import ColdBootReconciler
from alphaforge.reconciliation.state_store import (
    InMemoryStateStore,
    LocalPositionRecord,
    RecoverySnapshot,
)
from alphaforge.risk.enums import TradeSide


@given(
    strategy_id=st.text(
        min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Nd"))
    ),
    version=st.text(
        min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Nd", "Po"))
    ),
    symbol=st.sampled_from(["NIFTY", "BANKNIFTY", "FINNIFTY"]),
    role=st.sampled_from(list(OrderRole)),
    signal_id=st.text(
        min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Nd"))
    ),
)
@settings(max_examples=100)
def test_property_client_order_id_determinism(
    strategy_id: str,
    version: str,
    symbol: str,
    role: OrderRole,
    signal_id: str,
) -> None:
    """Property: Same canonical inputs always produce the identical client_order_id."""
    id1 = generate_client_order_id(strategy_id, version, symbol, role, signal_id)
    id2 = generate_client_order_id(strategy_id, version, symbol, role, signal_id)
    assert id1 == id2
    assert id1.startswith(f"AF-{role.value[0]}-")
    assert len(id1) <= 32


@given(
    signal_id1=st.text(min_size=1, max_size=20, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    signal_id2=st.text(min_size=1, max_size=20, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
)
@settings(max_examples=50)
def test_property_client_order_id_signal_uniqueness(
    signal_id1: str,
    signal_id2: str,
) -> None:
    """Property: Distinct signal IDs produce distinct client_order_ids."""
    if signal_id1 != signal_id2:
        id1 = generate_client_order_id("STRAT", "1.0", "NIFTY", OrderRole.ENTRY, signal_id1)
        id2 = generate_client_order_id("STRAT", "1.0", "NIFTY", OrderRole.ENTRY, signal_id2)
        assert id1 != id2


@given(
    quantity=st.integers(min_value=1, max_value=10000),
    side=st.sampled_from([OrderSide.BUY, OrderSide.SELL]),
)
@settings(max_examples=50)
def test_property_paper_broker_duplicate_submission_single_order(
    quantity: int,
    side: OrderSide,
) -> None:
    """Property: Repeated identical submissions to PaperBroker return the same order."""
    broker = PaperBroker()
    client_id = "AF-E-PROP-DUP"
    req = BrokerOrderRequest(
        client_order_id=client_id,
        symbol="NIFTY",
        side=side,
        quantity=quantity,
        role=OrderRole.ENTRY,
    )
    ord1 = broker.submit_order(req)
    ord2 = broker.submit_order(req)
    ord3 = broker.submit_order(req)

    assert ord1.broker_order_id == ord2.broker_order_id == ord3.broker_order_id
    assert len(broker.get_open_orders()) == 1


@given(
    q1=st.integers(min_value=1, max_value=500),
    q2=st.integers(min_value=501, max_value=1000),
)
@settings(max_examples=50)
def test_property_collision_detection_fails_closed(q1: int, q2: int) -> None:
    """Property: Conflicting intent for the same client_order_id strictly raises collision error."""
    registry = IdempotencyRegistry()
    intent1 = OrderIntent(
        client_order_id="AF-E-COLLIDE-PROP",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-PROP",
        side=TradeSide.LONG,
        quantity=q1,
    )
    registry.register(intent1)

    intent2 = OrderIntent(
        client_order_id="AF-E-COLLIDE-PROP",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-PROP",
        side=TradeSide.LONG,
        quantity=q2,  # Conflicting quantity!
    )
    with pytest.raises(IdempotencyCollisionError):
        registry.register(intent2)


@given(
    mismatches=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=25)
def test_property_gate_blocks_on_any_mismatches(mismatches: int) -> None:
    """Property: ReconciliationGate strictly remains closed on any nonzero mismatch count."""
    gate = ReconciliationGate()
    now = datetime.now(UTC)

    result = ReconciliationResult(
        reconciliation_id="REC-PROP",
        timestamp=now,
        status=ReconciliationStatus.MISMATCH,
        local_order_count=mismatches,
        broker_order_count=mismatches,
        local_position_count=0,
        broker_position_count=0,
        matched_count=0,
        mismatch_count=mismatches,
        unknown_count=0,
        new_entries_allowed=False,
        manual_escalation_required=True,
        reason_code=ReconciliationReasonCode.POSITION_MISMATCH,
    )
    with contextlib.suppress(ReconciliationError):
        gate.open(result)

    assert not gate.is_open
    assert not gate.can_accept_new_entries()


@given(
    quantity=st.integers(min_value=1, max_value=500),
    side=st.sampled_from([TradeSide.LONG, TradeSide.SHORT]),
    symbol=st.sampled_from(["NIFTY", "BANKNIFTY", "FINNIFTY"]),
)
@settings(max_examples=35)
def test_property_stale_local_protection_cannot_bypass_gate(
    quantity: int,
    side: TradeSide,
    symbol: str,
) -> None:
    """Property: local is_protected==True without broker stop is strictly NOT PROTECTED."""
    broker = PaperBroker()
    store = InMemoryStateStore()
    gate = ReconciliationGate()
    reconciler = ColdBootReconciler(broker=broker, state_store=store, gate=gate)

    now = datetime.now(UTC)

    # Active position on broker without any resting stop
    broker.inject_external_position(
        BrokerPosition(
            position_id=f"POS-PROP-{symbol}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            average_price=Decimal("24000.00"),
            status="OPEN",
        )
    )

    # Local position falsely claims protection
    local_pos = LocalPositionRecord(
        position_id=f"POS-PROP-{symbol}",
        symbol=symbol,
        side=side,
        quantity=quantity,
        average_price=Decimal("24000.00"),
        status="OPEN",
        is_protected=True,
    )
    store.save_snapshot(
        RecoverySnapshot(
            schema_version=1,
            positions={symbol: local_pos},
            created_at=now,
        )
    )

    result = reconciler.reconcile()
    assert result.status != ReconciliationStatus.MATCHED
    assert result.new_entries_allowed is False
    assert gate.is_open is False
    assert result.manual_escalation_required is True


@given(
    q1=st.integers(min_value=1, max_value=500),
    q2=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=35)
def test_property_paper_broker_single_entry_invariant(q1: int, q2: int) -> None:
    """Property: Existing position + additional ENTRY never increases position quantity."""
    broker = PaperBroker()
    req1 = BrokerOrderRequest(
        client_order_id="AF-E-PROP-INIT",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=q1,
        role=OrderRole.ENTRY,
    )
    broker.submit_order(req1)
    broker.simulate_full_fill("AF-E-PROP-INIT", Decimal("24500.00"))

    # Active position quantity is q1
    assert broker.get_positions()[0].quantity == q1

    # Any second ENTRY must fail closed with BrokerPositionConflictError
    req2 = BrokerOrderRequest(
        client_order_id="AF-E-PROP-SECOND",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=q2,
        role=OrderRole.ENTRY,
    )
    with pytest.raises(BrokerPositionConflictError):
        broker.submit_order(req2)

    # Position quantity strictly remains q1
    assert broker.get_positions()[0].quantity == q1
