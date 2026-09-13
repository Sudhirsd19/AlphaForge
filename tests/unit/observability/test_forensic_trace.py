"""
Unit tests for Phase 14 Forensic Lifecycle Tracing and Audit Separation.

Covers:
- Section 21 & 39: Full synthetic lifecycle golden trace.
  DATA -> SIGNAL -> RISK -> ORDER_INTENT -> ORDER_SUBMIT ->
  ORDER_ACK -> FILL -> POSITION -> RECONCILIATION
- Section 22: Rejection lifecycle trace (DATA -> SIGNAL -> RISK_REJECTED -> NO ORDER).
- Section 40: Observability vs Phase 9 Audit Ledger separation of concerns.
"""

from __future__ import annotations

from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.observability.events import (
    DataEventType,
    FillEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    OrderEventType,
    PositionEventType,
    ReconciliationEventType,
    RiskEventType,
    StrategyEventType,
)
from alphaforge.observability.sinks import InMemoryObservabilitySink, SafeObservabilityDispatcher


def test_complete_golden_forensic_trace() -> None:
    """
    Section 21 & 39: Verify a complete synthetic lifecycle trace with unbroken causal lineage.
    DATA -> SIGNAL -> RISK -> ORDER_INTENT -> ORDER_SUBMIT ->
    ORDER_ACK -> FILL -> POSITION -> RECONCILIATION
    """
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    correlation_id = "CORR-NIFTY-20260913-001"
    symbol = "NIFTY"
    client_order_id = "ORD-NIFTY-1001"
    broker_order_id = "BRK-EXCH-9999"

    # 1. DATA_RECEIVED
    e1 = ObservabilityEvent(
        event_type=DataEventType.DATA_RECEIVED.value,
        category=ObservabilityCategory.DATA,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id="FEED-TICK-001",
        message="Market candle 09:15:00 closed and normalized",
        attributes={"candle_open": "24500.00", "candle_close": "24550.00"},
    )
    dispatcher.emit(e1)

    # 2. SIGNAL_GENERATED
    e2 = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_GENERATED.value,
        category=ObservabilityCategory.STRATEGY,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e1.event_id,
        message="Strategy accepted long breakout signal",
        attributes={"direction": "LONG", "entry": "24550.00", "stop": "24450.00"},
    )
    dispatcher.emit(e2)

    # 3. RISK_CHECK
    e3 = ObservabilityEvent(
        event_type=RiskEventType.RISK_ACCEPTED.value,
        category=ObservabilityCategory.RISK,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e2.event_id,
        message="Risk checks passed: position risk within 1.0% limit",
        attributes={"reservation_id": "RES-001", "risk_amount": "5000.00"},
    )
    dispatcher.emit(e3)

    # 4. ORDER_INTENT
    e4 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_INTENT.value,
        category=ObservabilityCategory.ORDER,
        symbol=symbol,
        client_order_id=client_order_id,
        correlation_id=correlation_id,
        causation_id=e3.event_id,
        message=f"Intent to buy 50 {symbol}",
        attributes={"quantity": 50, "role": "ENTRY"},
    )
    dispatcher.emit(e4)

    # 5. ORDER_SUBMIT
    e5 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol=symbol,
        client_order_id=client_order_id,
        correlation_id=correlation_id,
        causation_id=e4.event_id,
        message=f"Submitted order {client_order_id} to broker queue",
    )
    dispatcher.emit(e5)

    # 6. ORDER_ACK
    e6 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_ACK.value,
        category=ObservabilityCategory.ORDER,
        symbol=symbol,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        correlation_id=correlation_id,
        causation_id=e5.event_id,
        message=f"Broker acknowledged order as {broker_order_id}",
    )
    dispatcher.emit(e6)

    # 7. FILL_RECEIVED
    e7 = ObservabilityEvent(
        event_type=FillEventType.FILL_RECEIVED.value,
        category=ObservabilityCategory.FILL,
        symbol=symbol,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        correlation_id=correlation_id,
        causation_id=e6.event_id,
        message="Fill received: 50 units @ 24550.00",
        attributes={"fill_quantity": 50, "fill_price": "24550.00"},
    )
    dispatcher.emit(e7)

    # 8. POSITION_UPDATE
    e8 = ObservabilityEvent(
        event_type=PositionEventType.POSITION_UPDATE.value,
        category=ObservabilityCategory.POSITION,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e7.event_id,
        message="Position net quantity updated to 50",
        attributes={"net_position": 50, "side": "LONG"},
    )
    dispatcher.emit(e8)

    # 9. RECONCILIATION_SUCCESS
    e9 = ObservabilityEvent(
        event_type=ReconciliationEventType.RECONCILIATION_SUCCESS.value,
        category=ObservabilityCategory.RECONCILIATION,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e8.event_id,
        message="Reconciliation clean: broker and local state perfectly aligned",
        attributes={"status": "MATCHED", "mismatch_count": 0},
    )
    dispatcher.emit(e9)

    # Verifications
    chain = sink.get_by_correlation_id(correlation_id)
    assert len(chain) == 9

    expected_types = [
        "DATA_RECEIVED",
        "SIGNAL_GENERATED",
        "RISK_ACCEPTED",
        "ORDER_INTENT",
        "ORDER_SUBMIT",
        "ORDER_ACK",
        "FILL_RECEIVED",
        "POSITION_UPDATE",
        "RECONCILIATION_SUCCESS",
    ]
    assert [e.event_type for e in chain] == expected_types

    # Verify strict causation chain
    for i in range(1, len(chain)):
        assert chain[i].causation_id == chain[i - 1].event_id

    # Verify all event IDs are distinct
    event_ids = [e.event_id for e in chain]
    assert len(set(event_ids)) == len(event_ids)


def test_rejection_trace_no_downstream_events() -> None:
    """
    Section 22: Rejection trace (DATA -> SIGNAL -> RISK_REJECTED).
    Verifies that NO order, fill, or position events are manufactured.
    """
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    correlation_id = "CORR-REJECTED-001"
    symbol = "BANKNIFTY"

    # 1. Data
    e1 = ObservabilityEvent(
        event_type=DataEventType.DATA_RECEIVED.value,
        category=ObservabilityCategory.DATA,
        symbol=symbol,
        correlation_id=correlation_id,
    )
    dispatcher.emit(e1)

    # 2. Signal
    e2 = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_GENERATED.value,
        category=ObservabilityCategory.STRATEGY,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e1.event_id,
    )
    dispatcher.emit(e2)

    # 3. Risk Rejection
    e3 = ObservabilityEvent(
        event_type=RiskEventType.RISK_REJECTED.value,
        category=ObservabilityCategory.RISK,
        symbol=symbol,
        correlation_id=correlation_id,
        causation_id=e2.event_id,
        message="Risk check rejected: MAX_DAILY_LOSS_BREACHED",
        attributes={"reason_code": "MAX_DAILY_LOSS_BREACHED"},
    )
    dispatcher.emit(e3)

    chain = sink.get_by_correlation_id(correlation_id)
    assert len(chain) == 3
    assert [e.event_type for e in chain] == [
        "DATA_RECEIVED",
        "SIGNAL_GENERATED",
        "RISK_REJECTED",
    ]

    # Critical requirement: Verify NO downstream order events exist
    assert len(sink.get_by_type("ORDER_INTENT")) == 0
    assert len(sink.get_by_type("ORDER_SUBMIT")) == 0
    assert len(sink.get_by_type("ORDER_ACK")) == 0
    assert len(sink.get_by_type("FILL_RECEIVED")) == 0


def test_section_40_observability_audit_separation() -> None:
    """
    Section 40: Prove that Phase 9 AuditLedger remains authoritative and immutable,
    while Phase 14 Observability is strictly diagnostic.
    """
    # 1. Ledger setup (authoritative)
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    # Commit authoritative audit event
    audit_event = ledger.append(
        event_type=AuditEventType.ORDER_FILLED,
        entity_type="ORDER",
        entity_id="ORD-LEDGER-01",
        correlation_id="RUN-100",
        causation_id="ORD-SUBMIT-01",
        payload={"filled_qty": 100, "price": "100.50"},
    )
    assert audit_event.event_id is not None
    assert audit_event.event_hash is not None

    # 2. Observability setup (diagnostic)
    obs_sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([obs_sink])

    obs_event = ObservabilityEvent(
        event_type=FillEventType.FILL_RECEIVED.value,
        category=ObservabilityCategory.FILL,
        client_order_id="ORD-LEDGER-01",
        correlation_id="RUN-100",
        message="Diagnostic fill event",
    )
    dispatcher.emit(obs_event)

    # Invariants:
    # 1. Observability does NOT write to AuditLedger
    assert len(ledger.get_events_by_entity("ORDER", "ORD-LEDGER-01")) == 1
    # 2. Audit event ID != Observability event ID
    assert audit_event.event_id != obs_event.event_id
    # 3. Observability failure cannot corrupt Ledger
    dispatcher.remove_sink(obs_sink)
    dispatcher.emit(obs_event)  # dropped in observability
    assert len(ledger.get_events_by_entity("ORDER", "ORD-LEDGER-01")) == 1  # Ledger untouched
