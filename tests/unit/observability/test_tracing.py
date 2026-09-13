"""
Unit tests for Phase 14 Causal Tracing and Context Management.

Covers:
- OBS5: Correlation propagation across scopes.
- OBS6: Causation linkage.
- OBS7: Order lifecycle reconstruction.
- OBS8: Timeout / retry distinction.
- Section 8: Concurrency test (concurrent trades with distinct correlation_ids).
- ADV-OBS-4: Two simultaneous trades on the same symbol preserve distinct lineages.
- ADV-OBS-5: Multiple partial fills remain individually traceable.
- ADV-OBS-6: Timeout followed by retry is visible without observability triggering the retry.
"""

from __future__ import annotations

import asyncio
import threading

from alphaforge.observability.context import TraceContext, trace_span
from alphaforge.observability.events import (
    FillEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    OrderEventType,
    StrategyEventType,
)
from alphaforge.observability.sinks import InMemoryObservabilitySink, SafeObservabilityDispatcher


def test_obs5_and_obs6_correlation_and_causation_propagation() -> None:
    """OBS5 & OBS6: Verify trace_span sets and restores correlation and causation IDs."""
    TraceContext.clear()
    assert TraceContext.get_correlation_id() is None
    assert TraceContext.get_causation_id() is None

    with trace_span("root_span", correlation_id="CORR-ROOT", causation_id="CAUS-ORIGIN"):
        assert TraceContext.get_correlation_id() == "CORR-ROOT"
        assert TraceContext.get_causation_id() == "CAUS-ORIGIN"

        # Nested span inheriting correlation_id but updating causation_id
        with trace_span("child_span", causation_id="CAUS-STEP-1"):
            assert TraceContext.get_correlation_id() == "CORR-ROOT"
            assert TraceContext.get_causation_id() == "CAUS-STEP-1"

        # After exiting child_span, parent context restored
        assert TraceContext.get_correlation_id() == "CORR-ROOT"
        assert TraceContext.get_causation_id() == "CAUS-ORIGIN"

    # After exiting root_span, cleared
    assert TraceContext.get_correlation_id() is None
    assert TraceContext.get_causation_id() is None


def test_obs7_order_lifecycle_reconstruction() -> None:
    """OBS7: Verify sequential reconstruction of order lifecycle events."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    cid = "ORD-LIFECYCLE-001"
    corr = "SIG-LIFECYCLE-100"

    # 1. Order Intent
    e1 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_INTENT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        correlation_id=corr,
        causation_id="SIGNAL-EVENT-1",
        message="Intent to buy 50 NIFTY",
    )
    dispatcher.emit(e1)

    # 2. Order Submit
    e2 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        correlation_id=corr,
        causation_id=e1.event_id,
        message="Order submitted to broker",
    )
    dispatcher.emit(e2)

    # 3. Order Ack
    e3 = ObservabilityEvent(
        event_type=OrderEventType.ORDER_ACK.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        broker_order_id="BRK-9999",
        correlation_id=corr,
        causation_id=e2.event_id,
        message="Order acknowledged by broker",
    )
    dispatcher.emit(e3)

    # 4. Fill
    e4 = ObservabilityEvent(
        event_type=FillEventType.FILL_RECEIVED.value,
        category=ObservabilityCategory.FILL,
        symbol="NIFTY",
        client_order_id=cid,
        broker_order_id="BRK-9999",
        correlation_id=corr,
        causation_id=e3.event_id,
        message="Order filled 50 @ 24500",
        attributes={"fill_qty": 50, "fill_price": "24500.00"},
    )
    dispatcher.emit(e4)

    events = sink.get_by_correlation_id(corr)
    assert len(events) == 4
    assert [e.event_type for e in events] == [
        "ORDER_INTENT",
        "ORDER_SUBMIT",
        "ORDER_ACK",
        "FILL_RECEIVED",
    ]

    # Verify unbroken causal chain
    assert events[1].causation_id == events[0].event_id
    assert events[2].causation_id == events[1].event_id
    assert events[3].causation_id == events[2].event_id


def test_obs8_and_adv_obs_6_timeout_retry_distinction() -> None:
    """OBS8 & ADV-OBS-6: Distinct visibility of timeout followed by retry."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    corr = "SIG-TIMEOUT-RETRY"
    cid = "ORD-TIMEOUT-001"

    # Submit
    e_sub = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        correlation_id=corr,
        causation_id="SIG-INTENT",
    )
    dispatcher.emit(e_sub)

    # Timeout
    e_timeout = ObservabilityEvent(
        event_type=OrderEventType.ORDER_TIMEOUT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        correlation_id=corr,
        causation_id=e_sub.event_id,
        message="Gateway timeout waiting for broker response",
    )
    dispatcher.emit(e_timeout)

    # Retry (diagnostic record of retry, not triggered by observability)
    e_retry = ObservabilityEvent(
        event_type=OrderEventType.ORDER_RETRY.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        correlation_id=corr,
        causation_id=e_timeout.event_id,
        message="Re-checking order status with idempotent client_order_id",
    )
    dispatcher.emit(e_retry)

    # Ack after retry
    e_ack = ObservabilityEvent(
        event_type=OrderEventType.ORDER_ACK.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        client_order_id=cid,
        broker_order_id="BRK-CONFIRMED",
        correlation_id=corr,
        causation_id=e_retry.event_id,
        message="Order confirmed existing on broker",
    )
    dispatcher.emit(e_ack)

    recorded = sink.get_by_correlation_id(corr)
    assert len(recorded) == 4
    types = [e.event_type for e in recorded]
    assert types == ["ORDER_SUBMIT", "ORDER_TIMEOUT", "ORDER_RETRY", "ORDER_ACK"]
    assert types.count("ORDER_TIMEOUT") == 1
    assert types.count("ORDER_RETRY") == 1


def test_section_8_contextvars_concurrency_and_adv_obs_4() -> None:
    """
    Section 8 & ADV-OBS-4: Two simultaneous trades for same symbol preserve distinct lineages
    across concurrent threads and async tasks without cross-contamination.
    """
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    trade_a_events: list[ObservabilityEvent] = []
    trade_b_events: list[ObservabilityEvent] = []

    def run_trade_a() -> None:
        with trace_span("span_A", correlation_id="CORR-TRADE-A", causation_id="ROOT-A"):
            for step in range(3):
                ev = ObservabilityEvent(
                    event_type=OrderEventType.ORDER_SUBMIT.value,
                    category=ObservabilityCategory.ORDER,
                    symbol="BTCUSDT",
                    correlation_id=TraceContext.get_correlation_id(),
                    causation_id=TraceContext.get_causation_id(),
                    client_order_id="ORD-BTC-A",
                    attributes={"step": step},
                )
                dispatcher.emit(ev)
                trade_a_events.append(ev)

    def run_trade_b() -> None:
        with trace_span("span_B", correlation_id="CORR-TRADE-B", causation_id="ROOT-B"):
            for step in range(3):
                ev = ObservabilityEvent(
                    event_type=OrderEventType.ORDER_SUBMIT.value,
                    category=ObservabilityCategory.ORDER,
                    symbol="BTCUSDT",
                    correlation_id=TraceContext.get_correlation_id(),
                    causation_id=TraceContext.get_causation_id(),
                    client_order_id="ORD-BTC-B",
                    attributes={"step": step},
                )
                dispatcher.emit(ev)
                trade_b_events.append(ev)

    t1 = threading.Thread(target=run_trade_a)
    t2 = threading.Thread(target=run_trade_b)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verify distinct correlation chains for same symbol
    assert all(e.correlation_id == "CORR-TRADE-A" for e in trade_a_events)
    assert all(e.correlation_id == "CORR-TRADE-B" for e in trade_b_events)
    assert len(sink.get_by_correlation_id("CORR-TRADE-A")) == 3
    assert len(sink.get_by_correlation_id("CORR-TRADE-B")) == 3


def test_async_nested_spans() -> None:
    """Verify contextvars isolation across async tasks and nested spans."""

    async def async_worker(task_id: str) -> list[str | None]:
        with trace_span(f"span_{task_id}", correlation_id=f"ASYNC-{task_id}"):
            await asyncio.sleep(0)  # cooperative yield
            corr = TraceContext.get_correlation_id()
            with trace_span(f"sub_{task_id}", causation_id=f"CAUS-{task_id}"):
                caus = TraceContext.get_causation_id()
            return [corr, caus]

    async def main() -> tuple[list[str | None], list[str | None]]:
        res_a, res_b = await asyncio.gather(async_worker("A"), async_worker("B"))
        return res_a, res_b

    res_a, res_b = asyncio.run(main())
    assert res_a == ["ASYNC-A", "CAUS-A"]
    assert res_b == ["ASYNC-B", "CAUS-B"]


def test_adv_obs_5_multiple_partial_fills() -> None:
    """ADV-OBS-5: Multiple partial fills for same order remain individually traceable."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    corr = "CORR-MULTI-FILL"
    cid = "ORD-PARTIAL-99"

    fills_data = [
        (10, "24500.00", "FILL-01"),
        (20, "24502.00", "FILL-02"),
        (20, "24505.00", "FILL-03"),
    ]

    for qty, px, fill_ref in fills_data:
        ev = ObservabilityEvent(
            event_type=FillEventType.FILL_RECEIVED.value,
            category=ObservabilityCategory.FILL,
            symbol="NIFTY",
            client_order_id=cid,
            correlation_id=corr,
            causation_id=fill_ref,
            message=f"Partial fill of {qty} units @ {px}",
            attributes={"fill_quantity": qty, "fill_price": px, "fill_id": fill_ref},
        )
        dispatcher.emit(ev)

    recorded_fills = sink.get_by_type("FILL_RECEIVED")
    assert len(recorded_fills) == 3

    # Distinct identities must be preserved
    fill_ids = [e.attributes["fill_id"] for e in recorded_fills]
    assert fill_ids == ["FILL-01", "FILL-02", "FILL-03"]
    # All share the same correlation chain
    assert all(e.correlation_id == corr for e in recorded_fills)


def test_same_correlation_concurrent_async_tasks_distinct_event_identities() -> None:
    """
    Verify:
    same correlation_id
    + parallel child tasks
    -> distinct event identities.

    Also proves deterministic reconstruction: replaying the identical asynchronous
    workflow from a clean context state reproduces the exact same sequence ordinals
    and event identities.
    """
    TraceContext.clear()
    shared_corr = "CORR-ASYNC-SHARED-999"

    async def child_task(task_index: int) -> ObservabilityEvent:
        # Cooperative yield to interleave execution
        await asyncio.sleep(0)
        return ObservabilityEvent(
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            category=ObservabilityCategory.STRATEGY,
            symbol="NIFTY",
            message=f"Child task {task_index} evaluating signal",
        )

    async def run_parallel_workflow() -> list[ObservabilityEvent]:
        with trace_span("parent_trading_workflow", correlation_id=shared_corr):
            # Spawn parallel child tasks under the same parent correlation context
            tasks = [asyncio.create_task(child_task(i)) for i in range(5)]
            return await asyncio.gather(*tasks)

    # Execution Run 1
    events_run_1 = asyncio.run(run_parallel_workflow())

    # 1. Verify same correlation_id inherited across all child tasks
    for ev in events_run_1:
        assert ev.correlation_id == shared_corr

    # 2. Verify all sequence ordinals are distinct (1, 2, 3, 4, 5)
    for ev in events_run_1:
        assert isinstance(ev.sequence, int)
    seqs_run_1 = [ev.sequence for ev in events_run_1 if ev.sequence is not None]
    assert len(seqs_run_1) == 5
    assert len(set(seqs_run_1)) == 5
    assert sorted(seqs_run_1) == [1, 2, 3, 4, 5]

    # 3. Verify all event IDs are pairwise distinct
    event_ids_run_1 = [ev.event_id for ev in events_run_1]
    assert len(set(event_ids_run_1)) == 5

    # 4. Verify deterministic reconstruction:
    # Reset context and re-run identical parallel workflow
    TraceContext.clear()
    events_run_2 = asyncio.run(run_parallel_workflow())

    seqs_run_2 = [ev.sequence for ev in events_run_2]
    event_ids_run_2 = [ev.event_id for ev in events_run_2]

    assert seqs_run_2 == seqs_run_1
    assert event_ids_run_2 == event_ids_run_1

    # 5. Verify serialized round-trip reconstruction retains bit-exact IDs
    for ev in events_run_1:
        reconstructed = ObservabilityEvent(**ev.to_dict())
        assert reconstructed.event_id == ev.event_id
        assert reconstructed.sequence == ev.sequence
