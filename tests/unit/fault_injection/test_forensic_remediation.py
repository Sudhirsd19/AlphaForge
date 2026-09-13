"""
Phase 12 — Forensic Remediation Tests.
Validates the three critical forensic blockers:
1. FI-H1, FI-S1: CorruptingLedgerStorage actually injects hash/sequence corruption.
2. FI-F1, FI-F2, FI-F3: Logical fill identity detects duplicates across different sequences
   and preserves distinct partial fills.
3. FI-P1..FI-P5 (PP1..PP5): Position provenance validation enforcing valid orders,
   matching symbols/sides, and bounded quantities.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
)
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole, generate_client_order_id
from alphaforge.fault_injection.injectors import CorruptingLedgerStorage
from alphaforge.fault_injection.invariants import (
    assert_hash_chain_intact,
    assert_no_duplicate_fills,
    assert_no_phantom_positions,
    assert_sequence_contiguous,
)
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.serialization import compute_event_hash
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.enums import TradeSide

# ==============================================================================
# BLOCKER 1: Actual Hash / Sequence Corruption Injector Tests
# ==============================================================================


class TestBlocker1ActualCorruptionInjector:
    """Proves CorruptingLedgerStorage actually injects corruption during append."""

    def test_FI_H1_hash_corruption_is_actually_injected(self) -> None:
        """FI-H1: Hash corruption injected at Nth event causes integrity validation to fail."""
        storage = InMemoryLedgerStorage()
        injector = CorruptingLedgerStorage(storage, corrupt_hash_at=2)
        ledger = AuditLedger(injector, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        # Append 3 valid events
        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-001",
            correlation_id="CORR-001",
            causation_id="ROOT",
            payload={"symbol": "NIFTY", "idx": 1},
            event_timestamp=base,
        )
        ev2 = ledger.append(
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-001",
            correlation_id="CORR-001",
            causation_id=ev1.event_id,
            payload={"symbol": "NIFTY", "qty": 50},
            event_timestamp=base + timedelta(seconds=10),
        )
        ledger.append(
            event_type=AuditEventType.ORDER_SUBMITTED,
            entity_type="ORDER",
            entity_id="ORD-001",
            correlation_id="CORR-001",
            causation_id=ev2.event_id,
            payload={"symbol": "NIFTY", "broker": "PAPER"},
            event_timestamp=base + timedelta(seconds=20),
        )

        stored_events = injector.read_all()
        assert len(stored_events) == 3

        # Event 1 is intact
        assert stored_events[0].event_hash == ev1.event_hash

        # Event 2 has been corrupted by the injector
        assert stored_events[1].event_hash != ev2.event_hash
        assert stored_events[1].sequence_number == ev2.sequence_number
        assert stored_events[1].entity_id == ev2.entity_id

        # The caller-owned ev2 object was NOT mutated (immutability preserved)
        assert ev2.event_hash != stored_events[1].event_hash

        # Recomputed hash does not match the corrupted hash
        recomputed = compute_event_hash(
            schema_version=stored_events[1].schema_version,
            sequence_number=stored_events[1].sequence_number,
            event_timestamp=stored_events[1].event_timestamp,
            event_type=stored_events[1].event_type,
            entity_type=stored_events[1].entity_type,
            entity_id=stored_events[1].entity_id,
            correlation_id=stored_events[1].correlation_id,
            causation_id=stored_events[1].causation_id,
            payload=dict(stored_events[1].payload),
            previous_event_hash=stored_events[1].previous_event_hash,
        )
        assert recomputed != stored_events[1].event_hash

        # Ledger verification detects corruption and fails
        result = ledger.verify_chain()
        assert result.valid is False
        assert result.corruption_detected is True

        # Invariant assertion detects hash corruption
        with pytest.raises(AssertionError):
            assert_hash_chain_intact(stored_events)

    def test_FI_S1_sequence_corruption_is_actually_injected(self) -> None:
        """FI-S1: Sequence corruption injected at Nth event causes sequence validation to fail."""
        storage = InMemoryLedgerStorage()
        injector = CorruptingLedgerStorage(storage, corrupt_sequence_at=2)
        ledger = AuditLedger(injector, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        # Append 3 valid events
        ev1 = ledger.append(
            event_type=AuditEventType.SIGNAL_GENERATED,
            entity_type="SIGNAL",
            entity_id="SIG-001",
            correlation_id="CORR-001",
            causation_id="ROOT",
            payload={"idx": 1},
            event_timestamp=base,
        )
        ev2 = ledger.append(
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="ORD-001",
            correlation_id="CORR-001",
            causation_id=ev1.event_id,
            payload={"qty": 50},
            event_timestamp=base + timedelta(seconds=10),
        )
        ledger.append(
            event_type=AuditEventType.ORDER_SUBMITTED,
            entity_type="ORDER",
            entity_id="ORD-001",
            correlation_id="CORR-001",
            causation_id=ev2.event_id,
            payload={"broker": "PAPER"},
            event_timestamp=base + timedelta(seconds=20),
        )

        stored_events = injector.read_all()
        assert len(stored_events) == 3

        # Event 1 sequence is 1
        assert stored_events[0].sequence_number == 1

        # Event 2 sequence was corrupted by injector (+999 -> 1001)
        assert stored_events[1].sequence_number == 1001
        assert ev2.sequence_number == 2  # Original object not mutated

        # Sequence contiguous check fails
        with pytest.raises(AssertionError, match="Sequence gap"):
            assert_sequence_contiguous([e.sequence_number for e in stored_events])

        # Hash chain check also fails because sequence numbers are part of hash
        with pytest.raises(AssertionError):
            assert_hash_chain_intact(stored_events)

        # Ledger verification detects sequence corruption
        result = ledger.verify_chain()
        assert result.valid is False
        assert result.corruption_detected is True


# ==============================================================================
# BLOCKER 2: Strengthen Duplicate Fill Invariant Tests
# ==============================================================================


class TestBlocker2DuplicateFillInvariant:
    """Proves assert_no_duplicate_fills detects logical duplicates regardless of sequence."""

    def test_FI_F1_same_logical_fill_different_sequence_detected(self) -> None:
        """FI-F1: Duplicate logical fill with different ledger sequence is caught."""
        ts = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
        fill1 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 50,
            "average_price": Decimal("24000.00"),
            "timestamp": ts,
            "sequence": 1,  # Sequence 1
        }
        fill2 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 50,
            "average_price": Decimal("24000.00"),
            "timestamp": ts,
            "sequence": 42,  # Different sequence 42!
        }

        # Must detect as duplicate fill despite sequence difference
        with pytest.raises(AssertionError, match="Duplicate fill detected for order 'ORD-NIFTY-1'"):
            assert_no_duplicate_fills([fill1, fill2])

    def test_FI_F2_legitimate_partial_fills_remain_distinct(self) -> None:
        """FI-F2: Two legitimate partial fills are NOT falsely flagged as duplicates."""
        base_ts = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
        partial_fill_1 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 25,
            "average_price": Decimal("24000.00"),
            "timestamp": base_ts,
            "fill_index": 1,
            "remaining_quantity": 25,
        }
        partial_fill_2 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 25,
            "average_price": Decimal("24005.00"),  # Different execution price
            "timestamp": base_ts + timedelta(seconds=15),  # Different timestamp
            "fill_index": 2,
            "remaining_quantity": 0,
        }

        # Legitimate partial fills must PASS
        assert_no_duplicate_fills([partial_fill_1, partial_fill_2])

    def test_FI_F2_partial_fills_same_price_different_time_remain_distinct(self) -> None:
        """FI-F2: Partial fills with identical quantity/price but distinct timestamps pass."""
        base_ts = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
        partial_1 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 25,
            "average_price": Decimal("24000.00"),
            "timestamp": base_ts,
            "remaining_quantity": 25,
        }
        partial_2 = {
            "order_id": "ORD-NIFTY-1",
            "filled_quantity": 25,
            "average_price": Decimal("24000.00"),
            "timestamp": base_ts + timedelta(seconds=30),  # Distinct execution
            "remaining_quantity": 0,
        }

        assert_no_duplicate_fills([partial_1, partial_2])

    def test_FI_F3_repeated_fill_after_restart_detected(self) -> None:
        """FI-F3: Re-receiving the same fill after restart is flagged as duplicate."""
        ts = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
        original_fill = {
            "fill_id": "BROKER-EXEC-789",
            "order_id": "ORD-001",
            "quantity": 50,
            "price": Decimal("24000"),
            "timestamp": ts,
        }
        restarted_duplicate_fill = {
            "fill_id": "BROKER-EXEC-789",
            "order_id": "ORD-001",
            "quantity": 50,
            "price": Decimal("24000"),
            "timestamp": ts,
        }

        with pytest.raises(AssertionError, match="Duplicate fill detected"):
            assert_no_duplicate_fills([original_fill, restarted_duplicate_fill])


# ==============================================================================
# BLOCKER 3: Correct Position Provenance Invariant Tests (PP1–PP5)
# ==============================================================================


def _make_broker_order(
    client_order_id: str = "ORD-001",
    broker_order_id: str = "BO-001",
    symbol: str = "NIFTY",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 50,
    filled_quantity: int = 50,
    status: BrokerOrderStatus = BrokerOrderStatus.FILLED,
    average_price: Decimal | None = Decimal("24000.00"),
) -> BrokerOrder:
    now = datetime.now(UTC)
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled_quantity=filled_quantity,
        status=status,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
        average_price=average_price,
        created_at=now,
        updated_at=now,
    )


class TestBlocker3PositionProvenanceInvariant:
    """Proves assert_no_phantom_positions enforces strict explicit provenance (PP1-PP8)."""

    def test_PP1_valid_position_provenance(self) -> None:
        """PP1: Explicit valid position -> valid order/fill -> PASS."""
        # Path A: Explicit origin_order_id on position
        client_id = generate_client_order_id("TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-PP1")
        pos_a = {
            "position_id": "POS-NIFTY-1",
            "origin_order_id": client_id,
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        order_a = _make_broker_order(
            client_order_id=client_id,
            broker_order_id="BO-001",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
            average_price=Decimal("24000.00"),
        )
        assert_no_phantom_positions([pos_a], [order_a])

        # Path B: Explicit position_id reference on execution
        pos_b = BrokerPosition(
            position_id="POS-NIFTY-2",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24000.00"),
            status="OPEN",
        )
        fill_b = {
            "position_id": "POS-NIFTY-2",
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 50,
        }
        assert_no_phantom_positions([pos_b], [fill_b])

    def test_PP2_nonexistent_origin_order_fails(self) -> None:
        """PP2: Position with nonexistent origin order -> FAIL."""
        pos = {
            "position_id": "POS-NIFTY-1",
            "origin_order_id": "ORD-NONEXISTENT",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        order = _make_broker_order(
            client_order_id="ORD-ACTUAL-001",
            broker_order_id="BO-001",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )
        with pytest.raises(AssertionError, match="references non-existent order"):
            assert_no_phantom_positions([pos], [order])

    def test_PP3_quantity_exceeds_originating_execution_fails(self) -> None:
        """PP3: Position quantity exceeds originating execution quantity -> FAIL."""
        pos = {
            "position_id": "POS-NIFTY-1",
            "origin_order_id": "ORD-001",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 100,  # Claims 100!
        }
        order = _make_broker_order(
            client_order_id="ORD-001",
            broker_order_id="BO-001",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,  # Only 50 executed
            status=BrokerOrderStatus.FILLED,
        )
        with pytest.raises(AssertionError, match="exceeds supported executed quantity"):
            assert_no_phantom_positions([pos], [order])

    def test_PP4_symbol_mismatch_fails(self) -> None:
        """PP4: Position symbol mismatch -> FAIL."""
        pos = {
            "position_id": "POS-NIFTY-1",
            "origin_order_id": "ORD-001",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        order = _make_broker_order(
            client_order_id="ORD-001",
            broker_order_id="BO-001",
            symbol="BANKNIFTY",  # Execution is BANKNIFTY
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )
        with pytest.raises(AssertionError, match="symbol"):
            assert_no_phantom_positions([pos], [order])

    def test_PP5_side_mismatch_fails(self) -> None:
        """PP5: Position side mismatch -> FAIL."""
        pos = {
            "position_id": "POS-NIFTY-1",
            "origin_order_id": "ORD-001",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        order = _make_broker_order(
            client_order_id="ORD-001",
            broker_order_id="BO-001",
            symbol="NIFTY",
            side=OrderSide.SELL,  # Execution is SELL / SHORT
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )
        with pytest.raises(AssertionError, match="side"):
            assert_no_phantom_positions([pos], [order])

    def test_PP6_no_provenance_identifiers_fails(self) -> None:
        """PP6: Non-zero position with no provenance identifiers -> FAIL."""
        pos = BrokerPosition(
            position_id="POS-GHOST-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("24000.00"),
            status="OPEN",
        )
        order = _make_broker_order(
            client_order_id="ORD-001",
            broker_order_id="BO-001",
            symbol="NIFTY",  # Matching symbol
            side=OrderSide.BUY,  # Matching side
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )
        # Provenance cannot be inferred solely from symbol and side
        with pytest.raises(
            AssertionError, match="has no explicit authoritative execution provenance"
        ):
            assert_no_phantom_positions([pos], [order])

        with pytest.raises(
            AssertionError, match="has no explicit authoritative execution provenance"
        ):
            assert_no_phantom_positions([pos], [])

    def test_PP7_same_symbol_and_side_different_orders(self) -> None:
        """
        PP7: Two executions have same symbol and side but belong to different orders;
        position belongs to only one -> only correct order may satisfy provenance.
        """
        order1 = _make_broker_order(
            client_order_id="ORD-1",
            broker_order_id="BO-001",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )
        order2 = _make_broker_order(
            client_order_id="ORD-2",
            broker_order_id="BO-002",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            filled_quantity=50,
            status=BrokerOrderStatus.FILLED,
        )

        # 7A: Position references ORD-1 with qty 50 -> PASS (only ORD-1 satisfies provenance)
        pos1 = {
            "position_id": "POS-1",
            "origin_order_id": "ORD-1",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        assert_no_phantom_positions([pos1], [order1, order2])

        # 7B: Position claims qty 100 referencing ORD-1 -> FAIL
        # Proves order2 cannot be aggregated merely because of same symbol/side
        pos_inflated = {
            "position_id": "POS-1",
            "origin_order_id": "ORD-1",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 100,
        }
        with pytest.raises(AssertionError, match="exceeds supported executed quantity"):
            assert_no_phantom_positions([pos_inflated], [order1, order2])

        # 7C: Position references non-existent ORD-3 -> FAIL
        # Proves same symbol/side from order1 & order2 cannot satisfy unlinked position
        pos_unlinked = {
            "position_id": "POS-1",
            "origin_order_id": "ORD-3",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,
        }
        with pytest.raises(AssertionError, match="references non-existent order"):
            assert_no_phantom_positions([pos_unlinked], [order1, order2])

    def test_PP8_multiple_partial_fills_from_same_originating_order(self) -> None:
        """
        PP8: Two legitimate partial fills from same originating order -> aggregate
        only those fills and PASS when quantity is supported.
        """
        fill1 = {
            "order_id": "ORD-PARTIAL-1",
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 25,
            "filled_quantity": 25,
        }
        fill2 = {
            "order_id": "ORD-PARTIAL-1",
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 25,
            "filled_quantity": 25,
        }
        unrelated_fill = {
            "order_id": "ORD-UNRELATED",
            "symbol": "NIFTY",
            "side": "BUY",
            "quantity": 50,
            "filled_quantity": 50,
        }

        pos = {
            "position_id": "POS-PARTIAL-1",
            "origin_order_id": "ORD-PARTIAL-1",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 50,  # 25 + 25 = 50
        }
        # Legitimate partial fills aggregate to support 50
        assert_no_phantom_positions([pos], [fill1, fill2, unrelated_fill])

        # Position claiming 51 exceeds sum of fill1 + fill2 and cannot use unrelated_fill
        pos_excess = {
            "position_id": "POS-PARTIAL-1",
            "origin_order_id": "ORD-PARTIAL-1",
            "symbol": "NIFTY",
            "side": "LONG",
            "quantity": 51,
        }
        with pytest.raises(AssertionError, match="exceeds supported executed quantity"):
            assert_no_phantom_positions([pos_excess], [fill1, fill2, unrelated_fill])
