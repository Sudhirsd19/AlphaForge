"""
Unit and tamper tests for AlphaForge AuditLedger.
Verifies genesis initialization, hash chaining, monotonic sequence allocation,
event-level idempotency, query APIs, all 15 tamper vectors, and multi-threaded concurrency.
"""

import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from alphaforge.core.exceptions import (
    LedgerCorruptionError,
    LedgerIntegrityError,
)
from alphaforge.execution.enums import OrderState
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.ledger.adapters import OrderFSMTransitionAuditor
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.serialization import compute_logical_event_id
from alphaforge.ledger.storage import FileLedgerStorage, InMemoryLedgerStorage
from alphaforge.risk.enums import TradeSide


def test_audit_ledger_genesis_and_chaining() -> None:
    """Verify genesis event starts with sequence 1 and 'GENESIS' previous hash."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    ev1 = ledger.append(
        event_type=AuditEventType.SIGNAL_GENERATED,
        entity_type="SIGNAL",
        entity_id="SIG-001",
        correlation_id="CORR-1",
        causation_id="STRATEGY-V1",
        payload={"score": Decimal("0.85")},
    )
    assert ev1.sequence_number == 1
    assert ev1.previous_event_hash == "GENESIS"

    ev2 = ledger.append(
        event_type=AuditEventType.RISK_CHECK,
        entity_type="RISK",
        entity_id="RISK-001",
        correlation_id="CORR-1",
        causation_id=ev1.event_id,
        payload={"approved": True},
    )
    assert ev2.sequence_number == 2
    assert ev2.previous_event_hash == ev1.event_hash

    # Verify chain
    result = ledger.verify_chain()
    assert result.valid is True
    assert result.event_count == 2
    assert result.first_sequence == 1
    assert result.last_sequence == 2


def test_audit_ledger_idempotency_same_event() -> None:
    """Verify appending identical logical event is idempotent: returns existing event."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    ev1 = ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    assert len(ledger) == 1

    # Re-submit exact same event
    ev2 = ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )
    assert ev1.event_id == ev2.event_id
    assert ev1.sequence_number == ev2.sequence_number
    assert len(ledger) == 1
    assert storage.count() == 1


def test_audit_ledger_idempotency_conflicting_payload_rejected() -> None:
    """Verify submitting same logical event with conflicting payload raises LedgerIntegrityError."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    ev1 = ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload={"quantity": 50},
    )

    # Force conflicting payload under same event_id collision
    new_payload = {"quantity": 100}
    target_event_id = compute_logical_event_id(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-1",
        correlation_id="CORR-1",
        causation_id="SIGNAL-1",
        payload=new_payload,
    )
    ledger._events_by_id[target_event_id] = ev1

    with pytest.raises(LedgerIntegrityError, match="Conflicting payload"):
        ledger.append(
            event_type=AuditEventType.ORDER_CREATED,
            entity_type="ORDER",
            entity_id="AF-E-1",
            correlation_id="CORR-1",
            causation_id="SIGNAL-1",
            payload=new_payload,
        )


def test_audit_ledger_queries() -> None:
    """Verify query APIs by event_id, sequence_number, entity, and correlation_id."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    ev1 = ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-100",
        correlation_id="TRADE-GROUP-1",
        causation_id="SIGNAL-1",
        payload={"qty": 50},
    )
    ev2 = ledger.append(
        event_type=AuditEventType.ORDER_SUBMITTED,
        entity_type="ORDER",
        entity_id="AF-E-100",
        correlation_id="TRADE-GROUP-1",
        causation_id=ev1.event_id,
        payload={"attempt": 1},
    )
    ev3 = ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="AF-E-200",
        correlation_id="TRADE-GROUP-2",
        causation_id="SIGNAL-2",
        payload={"qty": 25},
    )

    # Query by ID
    assert ledger.get_event(ev1.event_id) == ev1
    assert ledger.get_event("NONEXISTENT") is None

    # Query by sequence
    assert ledger.get_event_by_sequence(1) == ev1
    assert ledger.get_event_by_sequence(2) == ev2
    assert ledger.get_event_by_sequence(99) is None

    # Query by entity
    order_100_events = ledger.get_events_by_entity("ORDER", "AF-E-100")
    assert order_100_events == (ev1, ev2)

    # Query by correlation ID
    group1_events = ledger.get_events_by_correlation_id("TRADE-GROUP-1")
    assert group1_events == (ev1, ev2)
    group2_events = ledger.get_events_by_correlation_id("TRADE-GROUP-2")
    assert group2_events == (ev3,)


# =========================================================================
# SECTION 25: TAMPER TESTS (ALL 15 REQUIRED TAMPER VECTORS)
# =========================================================================


def _build_tamper_setup(tmp_path: Path) -> tuple[Path, AuditLedger]:
    p = tmp_path / "tamper_ledger.jsonl"
    storage = FileLedgerStorage(p)
    ledger = AuditLedger(storage)

    ledger.append(
        event_type=AuditEventType.ORDER_CREATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="C-1",
        causation_id="CAUS-1",
        payload={"price": Decimal("24000.00"), "qty": 50},
    )
    ledger.append(
        event_type=AuditEventType.ORDER_VALIDATED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="C-1",
        causation_id="CAUS-2",
        payload={"validated": True},
    )
    ledger.append(
        event_type=AuditEventType.ORDER_FILLED,
        entity_type="ORDER",
        entity_id="ORD-1",
        correlation_id="C-1",
        causation_id="CAUS-3",
        payload={"fill_qty": 50},
    )
    return p, ledger


def test_tamper_1_modified_payload(tmp_path: Path) -> None:
    """Tamper 1: Modifying payload in historical record fails verification."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    # Mutate payload of event 1
    lines[0] = lines[0].replace("24000.00", "99999.99")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code in ("EVENT_ID_CORRUPTED", "EVENT_HASH_CORRUPTED")


def test_tamper_2_modified_event_hash(tmp_path: Path) -> None:
    """Tamper 2: Modifying event_hash in historical record fails verification."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    data = json.loads(lines[1])
    data["event_hash"] = "0" * 64
    lines[1] = json.dumps(data)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code == "EVENT_HASH_CORRUPTED"


def test_tamper_3_modified_previous_event_hash(tmp_path: Path) -> None:
    """Tamper 3: Modifying previous_event_hash breaks linkage."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    data = json.loads(lines[1])
    data["previous_event_hash"] = "1" * 64
    lines[1] = json.dumps(data)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code in ("HASH_LINK_CORRUPTED", "EVENT_HASH_CORRUPTED")


def test_tamper_4_changed_sequence_number(tmp_path: Path) -> None:
    """Tamper 4: Changing sequence number is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"sequence_number":2', '"sequence_number":9')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code == "SEQUENCE_DISCONTINUITY"


def test_tamper_5_sequence_gap(tmp_path: Path) -> None:
    """Tamper 5: Sequence gap is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    # Delete event 2, leaving sequence 1 followed by sequence 3
    lines.pop(1)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code in ("SEQUENCE_DISCONTINUITY", "HASH_LINK_CORRUPTED")


def test_tamper_6_duplicate_sequence(tmp_path: Path) -> None:
    """Tamper 6: Duplicate sequence number is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace('"sequence_number":3', '"sequence_number":2')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code == "SEQUENCE_DISCONTINUITY"


def test_tamper_7_record_deletion(tmp_path: Path) -> None:
    """Tamper 7: Deleting historical record breaks chain linkage."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.pop(0)  # Delete first event
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


def test_tamper_8_record_reordering(tmp_path: Path) -> None:
    """Tamper 8: Swapping or reordering records fails verification."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


def test_tamper_9_malformed_json(tmp_path: Path) -> None:
    """Tamper 9: Malformed JSON syntax in ledger file fails closed."""
    p, _ = _build_tamper_setup(tmp_path)
    with p.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")

    with pytest.raises(LedgerCorruptionError):
        AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=True)


def test_tamper_10_truncated_final_record(tmp_path: Path) -> None:
    """Tamper 10: Incomplete / truncated final line fails closed."""
    p, _ = _build_tamper_setup(tmp_path)
    with p.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "EVT-INCOMPLETE", "sequen\n')

    with pytest.raises(LedgerCorruptionError):
        AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=True)


def test_tamper_11_corrupted_timestamp(tmp_path: Path) -> None:
    """Tamper 11: Tampering with timestamp changes canonical hash."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("2026", "2025")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True
    assert res.error_code == "EVENT_HASH_CORRUPTED"


def test_tamper_12_corrupted_event_type(tmp_path: Path) -> None:
    """Tamper 12: Tampering with event_type is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("ORDER_CREATED", "ORDER_FILLED")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


def test_tamper_13_corrupted_entity_id(tmp_path: Path) -> None:
    """Tamper 13: Tampering with entity_id is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("ORD-1", "ORD-999")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


def test_tamper_14_corrupted_correlation_id(tmp_path: Path) -> None:
    """Tamper 14: Tampering with correlation_id is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"correlation_id":"C-1"', '"correlation_id":"C-TAMPERED"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


def test_tamper_15_corrupted_payload_decimal(tmp_path: Path) -> None:
    """Tamper 15: Tampering with Decimal precision in payload is detected."""
    p, _ = _build_tamper_setup(tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("24000.00", "24000.001")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reloaded_ledger = AuditLedger(FileLedgerStorage(p), auto_verify_on_startup=False)
    res = reloaded_ledger.verify_chain()
    assert res.valid is False
    assert res.corruption_detected is True


# =========================================================================
# SECTION 27: CONCURRENCY TEST
# =========================================================================


def test_audit_ledger_concurrency_stress(tmp_path: Path) -> None:
    """
    Section 27 Concurrency Test:
    100 concurrent append operations across 10 threads.
    Guarantees:
    - Exactly 100 unique sequence numbers 1..100 without duplicates or gaps.
    - Completely valid cryptographic hash chain.
    """
    ledger_path = tmp_path / "concurrent_ledger.jsonl"
    storage = FileLedgerStorage(ledger_path)
    ledger = AuditLedger(storage)

    total_ops = 100
    errors: list[Exception] = []

    def worker(worker_idx: int, count: int) -> None:
        try:
            for i in range(count):
                ledger.append(
                    event_type=AuditEventType.ORDER_CREATED,
                    entity_type="ORDER",
                    entity_id=f"CONCUR-W{worker_idx}-{i}",
                    correlation_id=f"CORR-{worker_idx}",
                    causation_id=f"CAUS-{worker_idx}-{i}",
                    payload={"worker": worker_idx, "i": i},
                )
        except Exception as e:
            errors.append(e)

    threads: list[threading.Thread] = []
    num_threads = 10
    ops_per_thread = total_ops // num_threads

    for t_idx in range(num_threads):
        t = threading.Thread(target=worker, args=(t_idx, ops_per_thread))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(ledger) == total_ops

    # Verify sequence continuity 1..100
    events = storage.read_all()
    assert len(events) == total_ops
    sequences = [ev.sequence_number for ev in events]
    assert sequences == list(range(1, total_ops + 1))

    # Verify chain integrity
    res = ledger.verify_chain()
    assert res.valid is True
    assert res.event_count == total_ops


# =========================================================================
# SECTION 28: FSM CAUSATION LINEAGE TESTS (FINDING 2)
# =========================================================================


def test_fsm_auditor_causal_lineage_first_event_origin() -> None:
    """Verify first FSM event gets deterministic origin causation (signal_id or ORIGIN marker)."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)
    auditor = OrderFSMTransitionAuditor(ledger)

    # Case A: Order with signal_id
    fsm_with_sig = OrderStateMachine(
        order_id="ORD-SIG-01",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-ALPHA-101",
        transition_listener=auditor.on_transition,
    )
    fsm_with_sig.transition(OrderState.VALIDATED, reason="Passed validation")
    events_sig = ledger.get_events_by_entity("ORDER", "ORD-SIG-01")
    assert len(events_sig) == 1
    assert events_sig[0].causation_id == "SIG-ALPHA-101"
    assert events_sig[0].correlation_id == "SIG-ALPHA-101"

    # Case B: Order without signal_id
    fsm_no_sig = OrderStateMachine(
        order_id="ORD-MANUAL-01",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        transition_listener=auditor.on_transition,
    )
    fsm_no_sig.transition(OrderState.VALIDATED, reason="Passed validation")
    events_manual = ledger.get_events_by_entity("ORDER", "ORD-MANUAL-01")
    assert len(events_manual) == 1
    assert events_manual[0].causation_id == "ORIGIN:ORD-MANUAL-01"
    assert events_manual[0].correlation_id == "ORD-MANUAL-01"

    # Case C: Explicitly registered causation
    auditor.register_order_causation("ORD-CUSTOM-01", "CUSTOM-UPSTREAM-TX")
    fsm_custom = OrderStateMachine(
        order_id="ORD-CUSTOM-01",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        transition_listener=auditor.on_transition,
    )
    fsm_custom.transition(OrderState.VALIDATED, reason="Passed validation")
    events_custom = ledger.get_events_by_entity("ORDER", "ORD-CUSTOM-01")
    assert len(events_custom) == 1
    assert events_custom[0].causation_id == "CUSTOM-UPSTREAM-TX"


def test_fsm_auditor_causal_lineage_sequential_transitions() -> None:
    """Verify second and subsequent transitions reference the immediately preceding event_id."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)
    auditor = OrderFSMTransitionAuditor(ledger)

    fsm = OrderStateMachine(
        order_id="ORD-CHAIN-01",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-999",
        transition_listener=auditor.on_transition,
    )

    # Transition 1: VALIDATED
    fsm.transition(OrderState.VALIDATED, reason="Validated")
    ev1 = ledger.get_events_by_entity("ORDER", "ORD-CHAIN-01")[0]
    assert ev1.causation_id == "SIG-999"

    # Transition 2: SUBMITTED -> causation_id must be ev1.event_id
    fsm.transition(OrderState.SUBMITTED, reason="Submitted to broker")
    ev2 = ledger.get_events_by_entity("ORDER", "ORD-CHAIN-01")[1]
    assert ev2.causation_id == ev1.event_id
    assert auditor.get_last_event_id("ORD-CHAIN-01") == ev2.event_id

    # Transition 3: ACKNOWLEDGED -> causation_id must be ev2.event_id
    fsm.transition(OrderState.ACKNOWLEDGED, reason="Broker acknowledged")
    ev3 = ledger.get_events_by_entity("ORDER", "ORD-CHAIN-01")[2]
    assert ev3.causation_id == ev2.event_id

    # Transition 4: FILLED -> causation_id must be ev3.event_id
    fsm.transition(OrderState.FILLED, reason="Executed")
    ev4 = ledger.get_events_by_entity("ORDER", "ORD-CHAIN-01")[3]
    assert ev4.causation_id == ev3.event_id


def test_fsm_auditor_separate_orders_do_not_share_lineage() -> None:
    """Verify interleaved transitions for multiple orders maintain isolated causal chains."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)
    auditor = OrderFSMTransitionAuditor(ledger)

    fsm_a = OrderStateMachine(
        order_id="ORD-AAA",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-AAA",
        transition_listener=auditor.on_transition,
    )
    fsm_b = OrderStateMachine(
        order_id="ORD-BBB",
        symbol="BANKNIFTY26MARFUT",
        side=TradeSide.SHORT,
        quantity=25,
        signal_id="SIG-BBB",
        transition_listener=auditor.on_transition,
    )

    # Interleaved operations
    fsm_a.transition(OrderState.VALIDATED)
    fsm_b.transition(OrderState.VALIDATED)
    fsm_a.transition(OrderState.SUBMITTED)
    fsm_b.transition(OrderState.SUBMITTED)
    fsm_a.transition(OrderState.ACKNOWLEDGED)
    fsm_b.transition(OrderState.ACKNOWLEDGED)

    events_a = ledger.get_events_by_entity("ORDER", "ORD-AAA")
    events_b = ledger.get_events_by_entity("ORDER", "ORD-BBB")

    assert len(events_a) == 3
    assert len(events_b) == 3

    # Order A lineage
    assert events_a[0].causation_id == "SIG-AAA"
    assert events_a[1].causation_id == events_a[0].event_id
    assert events_a[2].causation_id == events_a[1].event_id

    # Order B lineage
    assert events_b[0].causation_id == "SIG-BBB"
    assert events_b[1].causation_id == events_b[0].event_id
    assert events_b[2].causation_id == events_b[1].event_id

    # Cross-contamination check
    event_ids_a = {ev.event_id for ev in events_a}
    event_ids_b = {ev.event_id for ev in events_b}
    assert event_ids_a.isdisjoint(event_ids_b)
    for ev in events_a:
        assert ev.causation_id not in event_ids_b
    for ev in events_b:
        assert ev.causation_id not in event_ids_a


def test_fsm_auditor_reattaching_adapter_restores_lineage_from_ledger() -> None:
    """Verify fresh auditor instance checks ledger history and resumes causal chain seamlessly."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)

    auditor_1 = OrderFSMTransitionAuditor(ledger)
    fsm = OrderStateMachine(
        order_id="ORD-RESTART-01",
        symbol="NIFTY26MARFUT",
        side=TradeSide.LONG,
        quantity=50,
        signal_id="SIG-RESTART",
        transition_listener=auditor_1.on_transition,
    )

    fsm.transition(OrderState.VALIDATED)
    fsm.transition(OrderState.SUBMITTED)
    events_before = ledger.get_events_by_entity("ORDER", "ORD-RESTART-01")
    assert len(events_before) == 2
    last_event_before = events_before[-1]

    # Create a completely fresh auditor instance without prior in-memory state
    auditor_2 = OrderFSMTransitionAuditor(ledger)
    assert auditor_2.get_last_event_id("ORD-RESTART-01") is None

    # Attach fresh auditor and perform next transition
    fsm._transition_listener = auditor_2.on_transition
    fsm.transition(OrderState.ACKNOWLEDGED)

    events_after = ledger.get_events_by_entity("ORDER", "ORD-RESTART-01")
    assert len(events_after) == 3
    new_event = events_after[-1]

    # Fresh auditor recovered the previous event_id from ledger queries
    assert new_event.causation_id == last_event_before.event_id


def test_fsm_auditor_concurrent_transitions_remain_isolated() -> None:
    """Verify concurrent transitions across distinct orders preserve strict lineage isolation."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)
    auditor = OrderFSMTransitionAuditor(ledger)

    num_orders = 10
    errors: list[Exception] = []

    def run_order_lifecycle(idx: int) -> None:
        try:
            ord_id = f"CONCUR-ORD-{idx:02d}"
            sig_id = f"SIG-{idx:02d}"
            fsm = OrderStateMachine(
                order_id=ord_id,
                symbol="NIFTY26MARFUT",
                side=TradeSide.LONG,
                quantity=50,
                signal_id=sig_id,
                transition_listener=auditor.on_transition,
            )
            fsm.transition(OrderState.VALIDATED)
            fsm.transition(OrderState.SUBMITTED)
            fsm.transition(OrderState.ACKNOWLEDGED)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run_order_lifecycle, args=(i,)) for i in range(num_orders)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(ledger) == num_orders * 3

    for i in range(num_orders):
        ord_id = f"CONCUR-ORD-{i:02d}"
        sig_id = f"SIG-{i:02d}"
        events = ledger.get_events_by_entity("ORDER", ord_id)
        assert len(events) == 3
        assert events[0].causation_id == sig_id
        assert events[1].causation_id == events[0].event_id
        assert events[2].causation_id == events[1].event_id
