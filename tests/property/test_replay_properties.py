"""
Property-based tests for AlphaForge Phase 11 Replay Engine using Hypothesis.
Proves deterministic, mathematical, causal, and immutability invariants:
P32: Event Stream Result Hash Determinism
P33: Event Permutation Rejection
P34: Event Payload Mutation Detection
P35: Checkpoint-Resume Equivalence
P36: Source Immutability
P37: Contract Expiry Replay
P38: Illegal FSM Sequences Rejected
P39: Source Trace Mutation Always Detected
P40: Replay Trace Strictly Independent of Source Execution Trace
P41: Result Reconstruction Strictly Reproduces Canonical Result Hash
P42: Arbitrary BacktestResult Mutations Fail Result Hash Verification
P43: Full FSM Transition Chain Validity Over Legal Transitions
P44: Terminal State Resurrection Always Fails
P45: Missing Mandatory Trade Attributes Strictly Fail Closed
P46: State Fingerprint Invariance Across Intermediate Checkpoint Resumption
"""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquitySnapshot,
    QuantGateResult,
    QuantGateStatus,
    TraceEntry,
    TraceEventType,
    compute_result_canonical_hash,
)
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderState
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import (
    AuditEventType,
)
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.replay.engine import ReplayEngine
from alphaforge.replay.loader import ReplayArtifactSource
from alphaforge.replay.models import (
    ReplayConfig,
    ReplayManifest,
    ReplayMode,
    ReplayStatus,
)


# ---------------------------------------------------------------------------
# Synthetic Test Fixture Helpers
# ---------------------------------------------------------------------------
def _generate_synthetic_candles(n_candles: int = 40) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    price = Decimal("24000")
    for i in range(n_candles):
        ts = t0 + timedelta(minutes=3 * i)
        price += Decimal("6") if i % 2 == 0 else Decimal("-4")
        candles.append(
            MarketCandle(
                symbol="NIFTY",
                instrument_type=InstrumentType.INDEX,
                contract_id="NIFTY-SPOT",
                exchange_timestamp=ts,
                received_timestamp=ts + timedelta(milliseconds=10),
                timeframe="3m",
                open=price,
                high=price + Decimal("10"),
                low=price - Decimal("10"),
                close=price + Decimal("2"),
                volume=5000 + i * 50,
                open_interest=50000,
                source="NSE",
            )
        )
    return candles


def _run_backtest_fixture(
    n_candles: int = 40, warmup_bars: int = 15
) -> tuple[BacktestEngine, BacktestResult]:
    candles = _generate_synthetic_candles(n_candles)
    dataset = BacktestDataset(f"SYNTH_{n_candles}", candles)
    t_start = candles[0].exchange_timestamp
    t_end = candles[-1].exchange_timestamp + timedelta(minutes=3)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t_start,
        end_time=t_end,
        initial_capital=Decimal("1000000"),
        warmup_bars=warmup_bars,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, dataset)
    res = engine.run()
    return engine, res


def _build_sample_trade_ledger(
    symbol: str = "NIFTY",
    quantity: int = 10,
    gross_pnl: int = 1000,
    net_pnl: int = 975,
) -> AuditLedger:
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage)
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_TRD",
        correlation_id="RUN_TRD",
        causation_id="GENESIS",
        payload={"run_id": "RUN_TRD", "contract_id": symbol, "initial_capital": "1000000"},
        event_timestamp=t0,
    )
    e2 = ledger.append(
        event_type=AuditEventType.SIGNAL_GENERATED,
        entity_type="SIGNAL",
        entity_id="SIG_1",
        correlation_id="RUN_TRD",
        causation_id=e1.event_id,
        payload={
            "signal_id": "SIG_1",
            "direction": "LONG",
            "symbol": symbol,
            "entry_price": "24000",
            "stop_loss": "23900",
            "profit_target": "24200",
        },
        event_timestamp=t0 + timedelta(minutes=1),
    )
    e3 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_1",
        correlation_id="RUN_TRD",
        causation_id=e2.event_id,
        payload={
            "order_id": "ORD_1",
            "symbol": symbol,
            "side": "LONG",
            "quantity": quantity,
            "entry_price": "24000",
        },
        event_timestamp=t0 + timedelta(minutes=2),
    )
    e4 = ledger.append(
        event_type=AuditEventType.FILL_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_1",
        correlation_id="RUN_TRD",
        causation_id=e3.event_id,
        payload={
            "order_id": "ORD_1",
            "symbol": symbol,
            "side": "LONG",
            "quantity": quantity,
            "effective_price": "24000",
            "fee": "20",
            "slippage_loss": "5",
        },
        event_timestamp=t0 + timedelta(minutes=3),
    )
    e5 = ledger.append(
        event_type=AuditEventType.TRADE_CLOSED,
        entity_type="TRADE",
        entity_id="TRD_1",
        correlation_id="RUN_TRD",
        causation_id=e4.event_id,
        payload={
            "trade_id": "TRD_1",
            "symbol": symbol,
            "gross_pnl": str(gross_pnl),
            "net_pnl": str(net_pnl),
            "exit_reason": "SIGNAL",
        },
        event_timestamp=t0 + timedelta(minutes=4),
    )
    ledger.append(
        event_type=AuditEventType.BACKTEST_COMPLETED,
        entity_type="BACKTEST",
        entity_id="RUN_TRD",
        correlation_id="RUN_TRD",
        causation_id=e5.event_id,
        payload={"run_id": "RUN_TRD", "total_trades": 1, "final_equity": str(1000000 + net_pnl)},
        event_timestamp=t0 + timedelta(minutes=5),
    )
    return ledger


# ---------------------------------------------------------------------------
# P32: Event Stream Result Hash Determinism
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    n_candles=st.integers(min_value=35, max_value=50),
    warmup_bars=st.integers(min_value=12, max_value=18),
)
def test_p32_event_stream_result_hash_determinism(n_candles: int, warmup_bars: int) -> None:
    bt_engine, bt_res = _run_backtest_fixture(n_candles=n_candles, warmup_bars=warmup_bars)
    events = bt_engine.ledger._storage.read_all()
    assert len(events) >= 5

    source = ReplayArtifactSource(events=events, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL, checkpoint_interval=5)

    # Run 1
    engine1 = ReplayEngine(config, source)
    res1 = engine1.replay()

    # Run 2
    engine2 = ReplayEngine(config, source)
    res2 = engine2.replay()

    assert res1.status == ReplayStatus.PASS
    assert res2.status == ReplayStatus.PASS
    assert res1.replay_id == res2.replay_id
    assert res1.final_state_fingerprint == res2.final_state_fingerprint
    assert res1.events_processed == res2.events_processed == len(events)
    assert res1.events_verified == res2.events_verified == len(events)
    assert len(res1.checkpoints) == len(res2.checkpoints)
    for cp1, cp2 in zip(res1.checkpoints, res2.checkpoints, strict=True):
        assert cp1.state_fingerprint == cp2.state_fingerprint
        assert cp1.sequence_number == cp2.sequence_number


# ---------------------------------------------------------------------------
# P33: Event Permutation Rejection
# ---------------------------------------------------------------------------
@settings(max_examples=15, deadline=None)
@given(
    swap_pair=st.tuples(
        st.integers(min_value=0, max_value=3),
        st.integers(min_value=1, max_value=4),
    ).filter(lambda p: p[0] != p[1])
)
def test_p33_event_permutation_rejection(swap_pair: tuple[int, int]) -> None:
    bt_engine, bt_res = _run_backtest_fixture(n_candles=40, warmup_bars=15)
    events = list(bt_engine.ledger._storage.read_all())
    assert len(events) >= 5

    idx1, idx2 = swap_pair
    # Perform out-of-order permutation of events
    permuted_events = list(events)
    permuted_events[idx1], permuted_events[idx2] = permuted_events[idx2], permuted_events[idx1]

    source = ReplayArtifactSource(events=permuted_events, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL, verify_audit_hash_chain=True)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    # Out-of-order sequence or broken hash chain must be rejected
    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category in {
        "HASH_CHAIN_DIVERGENCE",
        "SEQUENCE_GAP",
        "SEQUENCE_DISCONTINUITY",
        "LINEAGE_DIVERGENCE",
        "TIMESTAMP_NON_MONOTONIC",
    }


# ---------------------------------------------------------------------------
# P34: Event Payload Mutation Detection
# ---------------------------------------------------------------------------
@settings(max_examples=15, deadline=None)
@given(
    target_idx=st.integers(min_value=1, max_value=4),
    tamper_salt=st.text(min_size=1, max_size=10, alphabet="abcdef0123456789"),
)
def test_p34_event_payload_mutation_detection(target_idx: int, tamper_salt: str) -> None:
    bt_engine, bt_res = _run_backtest_fixture(n_candles=40, warmup_bars=15)
    events = list(bt_engine.ledger._storage.read_all())
    assert len(events) > target_idx

    target_event = events[target_idx]
    # Mutate payload without altering event_hash (canonical hash mismatch)
    mutated_payload = dict(target_event.payload)
    mutated_payload["_tamper_marker"] = tamper_salt

    mutated_event = target_event.model_copy(update={"payload": mutated_payload})
    mutated_stream = list(events)
    mutated_stream[target_idx] = mutated_event

    source = ReplayArtifactSource(events=mutated_stream, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL, verify_audit_hash_chain=True)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category in {
        "CANONICAL_HASH_MISMATCH",
        "HASH_CHAIN_DIVERGENCE",
        "EVENT_ID_CORRUPTION",
    }


# ---------------------------------------------------------------------------
# P35: Checkpoint-Resume Equivalence
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    checkpoint_choice=st.integers(min_value=0, max_value=2),
)
def test_p35_checkpoint_resume_equivalence(checkpoint_choice: int) -> None:
    bt_engine, bt_res = _run_backtest_fixture(n_candles=45, warmup_bars=15)
    events = bt_engine.ledger._storage.read_all()
    assert len(events) >= 5

    source = ReplayArtifactSource(events=events, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL, checkpoint_interval=2)

    # 1. Fresh full replay
    fresh_engine = ReplayEngine(config, source)
    fresh_res = fresh_engine.replay()
    assert fresh_res.status == ReplayStatus.PASS
    assert len(fresh_res.checkpoints) >= 3

    # Pick intermediate checkpoint
    selected_cp = fresh_res.checkpoints[min(checkpoint_choice, len(fresh_res.checkpoints) - 1)]

    # 2. Resumed replay
    resume_engine = ReplayEngine(config, source)
    resumed_res = resume_engine.resume_from_checkpoint(selected_cp)

    assert resumed_res.status == ReplayStatus.PASS
    assert resumed_res.final_state_fingerprint == fresh_res.final_state_fingerprint
    assert resumed_res.final_state is not None
    assert fresh_res.final_state is not None
    assert resumed_res.final_state.orders == fresh_res.final_state.orders
    assert resumed_res.final_state.position == fresh_res.final_state.position
    assert resumed_res.final_state.portfolio == fresh_res.final_state.portfolio
    assert resumed_res.final_state.risk == fresh_res.final_state.risk


# ---------------------------------------------------------------------------
# P36: Source Immutability
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    n_candles=st.integers(min_value=35, max_value=45),
)
def test_p36_source_immutability(n_candles: int) -> None:
    bt_engine, bt_res = _run_backtest_fixture(n_candles=n_candles, warmup_bars=15)
    storage = InMemoryLedgerStorage()
    for evt in bt_engine.ledger._storage.read_all():
        storage.append(evt)

    events_before = storage.read_all()
    hashes_before = [e.event_hash for e in events_before]
    serialized_before = [e.model_dump_json() for e in events_before]
    storage_digest_before = hashlib.sha256("".join(serialized_before).encode("utf-8")).hexdigest()

    source = ReplayArtifactSource(events=events_before, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.PASS

    events_after = storage.read_all()
    hashes_after = [e.event_hash for e in events_after]
    serialized_after = [e.model_dump_json() for e in events_after]
    storage_digest_after = hashlib.sha256("".join(serialized_after).encode("utf-8")).hexdigest()

    # Source storage and event objects must remain 100% byte-for-byte immutable
    assert hashes_before == hashes_after
    assert serialized_before == serialized_after
    assert storage_digest_before == storage_digest_after


# ---------------------------------------------------------------------------
# P37: Contract Expiry Replay
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    expiry_offset_minutes=st.integers(min_value=15, max_value=45),
)
def test_p37_contract_expiry_replay(expiry_offset_minutes: int) -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    t_exp = t0 + timedelta(minutes=expiry_offset_minutes)

    evt1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_EXPIRY",
        correlation_id="RUN_EXPIRY",
        causation_id="GENESIS",
        payload={
            "run_id": "RUN_EXPIRY",
            "initial_capital": "1000000",
            "contract_id": "NIFTY26JANFUT",
            "expiry_datetime": t_exp.isoformat(),
        },
        event_timestamp=t0,
    )
    evt2 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD-EXP-1",
        correlation_id="RUN_EXPIRY",
        causation_id=evt1.event_id,
        payload={
            "order_id": "ORD-EXP-1",
            "symbol": "NIFTY26JANFUT",
            "quantity": 50,
            "side": "LONG",
            "order_state": "CREATED",
        },
        event_timestamp=t0 + timedelta(minutes=1),
    )
    evt3 = ledger.append(
        event_type=AuditEventType.FILL_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD-EXP-1",
        correlation_id="RUN_EXPIRY",
        causation_id=evt2.event_id,
        payload={
            "order_id": "ORD-EXP-1",
            "symbol": "NIFTY26JANFUT",
            "quantity": 50,
            "side": "LONG",
            "effective_price": "24000",
            "fee": "20",
            "slippage_loss": "5",
        },
        event_timestamp=t0 + timedelta(minutes=2),
    )
    # Event at expiry
    evt4 = ledger.append(
        event_type=AuditEventType.TRADE_CLOSED,
        entity_type="TRADE",
        entity_id="TRD-EXP-1",
        correlation_id="RUN_EXPIRY",
        causation_id=evt3.event_id,
        payload={
            "trade_id": "TRD-EXP-1",
            "symbol": "NIFTY26JANFUT",
            "net_pnl": "1000",
        },
        event_timestamp=t_exp,
    )
    ledger.append(
        event_type=AuditEventType.BACKTEST_COMPLETED,
        entity_type="BACKTEST",
        entity_id="RUN_EXPIRY",
        correlation_id="RUN_EXPIRY",
        causation_id=evt4.event_id,
        payload={"status": "COMPLETED"},
        event_timestamp=t_exp + timedelta(minutes=5),
    )

    source = ReplayArtifactSource(events=ledger._storage.read_all())
    config = ReplayConfig(mode=ReplayMode.FULL)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.PASS
    assert res.final_state is not None
    assert res.final_state.contract.is_expired is True
    assert res.final_state.contract.post_expiry_fills == 0


# ---------------------------------------------------------------------------
# P38: Illegal FSM Sequences Rejected
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    illegal_target=st.sampled_from([OrderState.SUBMITTED, OrderState.ACKNOWLEDGED]),
)
def test_p38_illegal_fsm_sequences_rejected(illegal_target: OrderState) -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    evt1 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD-FSM-FAIL",
        correlation_id="RUN-FSM",
        causation_id="GENESIS",
        payload={
            "order_id": "ORD-FSM-FAIL",
            "symbol": "NIFTY",
            "quantity": 50,
            "side": "LONG",
            "order_state": "CREATED",
        },
        event_timestamp=t0,
    )
    # Order cancelled (terminal state)
    evt2 = ledger.append(
        event_type=AuditEventType.ORDER_CANCELLED,
        entity_type="ORDER",
        entity_id="ORD-FSM-FAIL",
        correlation_id="RUN-FSM",
        causation_id=evt1.event_id,
        payload={
            "order_id": "ORD-FSM-FAIL",
            "order_state": "CANCELLED",
        },
        event_timestamp=t0 + timedelta(minutes=1),
    )
    # Attempt resurrection into SUBMITTED or ACKNOWLEDGED
    ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD-FSM-FAIL",
        correlation_id="RUN-FSM",
        causation_id=evt2.event_id,
        payload={
            "order_id": "ORD-FSM-FAIL",
            "order_state": illegal_target.value,
        },
        event_timestamp=t0 + timedelta(minutes=2),
    )

    source = ReplayArtifactSource(events=ledger._storage.read_all())
    config = ReplayConfig(mode=ReplayMode.FULL, verify_fsm_transitions=True)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category in (
        "FSM_ILLEGAL_TRANSITION",
        "FSM_TERMINAL_RESURRECTION",
    )


# ---------------------------------------------------------------------------
# P39: Source Trace Mutation Always Detected
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    delta_price=st.integers(min_value=1, max_value=500),
)
def test_p39_source_trace_mutation_detected(delta_price: int) -> None:
    clean_ledger = _build_sample_trade_ledger()
    clean_events = clean_ledger._storage.read_all()
    baseline_res = ReplayEngine(ReplayConfig(), ReplayArtifactSource(events=clean_events)).replay()
    assert baseline_res.status == ReplayStatus.PASS
    assert baseline_res.final_state is not None
    assert len(baseline_res.final_state.trace) > 0

    mutated_trace = list(baseline_res.final_state.trace)
    orig = mutated_trace[0]
    mutated_trace[0] = orig.model_copy(
        update={"price": (orig.price or Decimal("24000")) + Decimal(delta_price)}
    )

    source = ReplayArtifactSource(
        events=clean_events,
        trace=mutated_trace,
    )
    config = ReplayConfig(verify_execution_trace=True)
    res = ReplayEngine(config, source).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "TRACE_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# P40: Replay Trace Strictly Independent of Source Execution Trace
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    forged_price=st.integers(min_value=100000, max_value=999999),
)
def test_p40_replay_trace_strictly_independent_of_source(forged_price: int) -> None:
    clean_ledger = _build_sample_trade_ledger()
    clean_events = clean_ledger._storage.read_all()

    # Replay with empty source trace
    source_clean = ReplayArtifactSource(events=clean_events, trace=())
    res_clean = ReplayEngine(ReplayConfig(), source_clean).replay()
    assert res_clean.status == ReplayStatus.PASS
    assert res_clean.final_state is not None

    # Replay with forged source trace injected
    fake_trace_entry = TraceEntry(
        timestamp=clean_events[0].event_timestamp,
        event_type=TraceEventType.ORDER_SIMULATED,
        entity_id="FORGED_ORD",
        symbol="NIFTY",
        price=Decimal(forged_price),
    )
    source_forged = ReplayArtifactSource(events=clean_events, trace=(fake_trace_entry,))
    res_forged = ReplayEngine(ReplayConfig(verify_execution_trace=False), source_forged).replay()
    assert res_forged.status == ReplayStatus.PASS
    assert res_forged.final_state is not None

    # Replay trace must be identical to clean replay trace, and NOT contain forged entry
    assert res_clean.final_state.trace == res_forged.final_state.trace
    for te in res_forged.final_state.trace:
        assert te.entity_id != "FORGED_ORD"
        assert te.price != Decimal(forged_price)


# ---------------------------------------------------------------------------
# P41: Result Reconstruction Strictly Reproduces Canonical Result Hash
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    gross_pnl=st.integers(min_value=100, max_value=10000),
    net_pnl=st.integers(min_value=50, max_value=9900),
)
def test_p41_result_reconstruction_reproduces_canonical_hash(gross_pnl: int, net_pnl: int) -> None:
    ledger = _build_sample_trade_ledger(gross_pnl=gross_pnl, net_pnl=net_pnl)
    events = ledger._storage.read_all()

    source1 = ReplayArtifactSource(events=events)
    res1 = ReplayEngine(ReplayConfig(), source1).replay()
    assert res1.status == ReplayStatus.PASS

    source2 = ReplayArtifactSource(events=events)
    res2 = ReplayEngine(ReplayConfig(), source2).replay()
    assert res2.status == ReplayStatus.PASS

    assert res1.final_state_fingerprint == res2.final_state_fingerprint
    assert res1.final_state is not None
    assert len(res1.final_state.trades) == 1
    assert res1.final_state.trades[0].gross_pnl == Decimal(gross_pnl)
    assert res1.final_state.trades[0].net_pnl == Decimal(net_pnl)


# ---------------------------------------------------------------------------
# P42: Arbitrary BacktestResult Mutations Fail Result Hash Verification
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    delta_pnl=st.integers(min_value=10, max_value=5000),
)
def test_p42_backtest_result_mutation_fails_result_hash(delta_pnl: int) -> None:
    bt_engine, bt_res = _run_backtest_fixture(35, 12)
    events = bt_engine.ledger._storage.read_all()

    # Mutate backtest result
    mutated_bt_res = bt_res.model_copy(
        update={
            "metrics": bt_res.metrics.model_copy(
                update={"net_pnl": bt_res.metrics.net_pnl + Decimal(delta_pnl)}
            )
        }
    )
    mutated_bt_res = mutated_bt_res.model_copy(
        update={"result_canonical_hash": compute_result_canonical_hash(mutated_bt_res)}
    )

    source = ReplayArtifactSource(events=events, backtest_result=mutated_bt_res)
    config = ReplayConfig(verify_result_hash=True)
    res = ReplayEngine(config, source).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "RESULT_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# P43: Full FSM Transition Chain Validity Over Legal Transitions
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    flow_choice=st.sampled_from(["SUBMITTED_REJECTED", "SUBMITTED_ACK_CANCELLED", "ORDER_FILLED"]),
)
def test_p43_fsm_legal_transitions_pass(flow_choice: str) -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_FSM43",
        correlation_id="RUN_FSM43",
        causation_id="GENESIS",
        payload={"run_id": "RUN_FSM43", "contract_id": "NIFTY"},
        event_timestamp=t0,
    )
    e2 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_43",
        correlation_id="RUN_FSM43",
        causation_id=e1.event_id,
        payload={"order_id": "ORD_43", "symbol": "NIFTY", "side": "LONG", "quantity": 5},
        event_timestamp=t0 + timedelta(minutes=1),
    )
    if flow_choice == "SUBMITTED_REJECTED":
        ledger.append(
            event_type=AuditEventType.ORDER_REJECTED,
            entity_type="ORDER",
            entity_id="ORD_43",
            correlation_id="RUN_FSM43",
            causation_id=e2.event_id,
            payload={"order_id": "ORD_43"},
            event_timestamp=t0 + timedelta(minutes=2),
        )
    elif flow_choice == "SUBMITTED_ACK_CANCELLED":
        e3 = ledger.append(
            event_type=AuditEventType.ORDER_ACKNOWLEDGED,
            entity_type="ORDER",
            entity_id="ORD_43",
            correlation_id="RUN_FSM43",
            causation_id=e2.event_id,
            payload={"order_id": "ORD_43"},
            event_timestamp=t0 + timedelta(minutes=2),
        )
        ledger.append(
            event_type=AuditEventType.ORDER_CANCELLED,
            entity_type="ORDER",
            entity_id="ORD_43",
            correlation_id="RUN_FSM43",
            causation_id=e3.event_id,
            payload={"order_id": "ORD_43"},
            event_timestamp=t0 + timedelta(minutes=3),
        )
    else:  # ORDER_FILLED
        ledger.append(
            event_type=AuditEventType.ORDER_FILLED,
            entity_type="ORDER",
            entity_id="ORD_43",
            correlation_id="RUN_FSM43",
            causation_id=e2.event_id,
            payload={
                "order_id": "ORD_43",
                "symbol": "NIFTY",
                "quantity": 5,
                "effective_price": "24000",
            },
            event_timestamp=t0 + timedelta(minutes=2),
        )

    source = ReplayArtifactSource(events=ledger._storage.read_all())
    res = ReplayEngine(ReplayConfig(verify_fsm_transitions=True), source).replay()
    assert res.status == ReplayStatus.PASS
    assert res.final_state is not None
    assert "ORD_43" in res.final_state.orders


# ---------------------------------------------------------------------------
# P44: Terminal State Resurrection Always Fails
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    terminal_event=st.sampled_from([AuditEventType.ORDER_REJECTED, AuditEventType.ORDER_CANCELLED]),
)
def test_p44_terminal_state_resurrection_fails(terminal_event: AuditEventType) -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_FSM44",
        correlation_id="RUN_FSM44",
        causation_id="GENESIS",
        payload={"run_id": "RUN_FSM44", "contract_id": "NIFTY"},
        event_timestamp=t0,
    )
    e2 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_44",
        correlation_id="RUN_FSM44",
        causation_id=e1.event_id,
        payload={"order_id": "ORD_44", "symbol": "NIFTY", "side": "LONG", "quantity": 1},
        event_timestamp=t0 + timedelta(minutes=1),
    )
    if terminal_event == AuditEventType.ORDER_CANCELLED:
        # Move to ACKNOWLEDGED first so CANCELLED is a legal transition
        e_ack = ledger.append(
            event_type=AuditEventType.ORDER_ACKNOWLEDGED,
            entity_type="ORDER",
            entity_id="ORD_44",
            correlation_id="RUN_FSM44",
            causation_id=e2.event_id,
            payload={"order_id": "ORD_44"},
            event_timestamp=t0 + timedelta(minutes=2),
        )
        e_term = ledger.append(
            event_type=AuditEventType.ORDER_CANCELLED,
            entity_type="ORDER",
            entity_id="ORD_44",
            correlation_id="RUN_FSM44",
            causation_id=e_ack.event_id,
            payload={"order_id": "ORD_44"},
            event_timestamp=t0 + timedelta(minutes=3),
        )
    else:
        # SUBMITTED -> REJECTED is legal
        e_term = ledger.append(
            event_type=AuditEventType.ORDER_REJECTED,
            entity_type="ORDER",
            entity_id="ORD_44",
            correlation_id="RUN_FSM44",
            causation_id=e2.event_id,
            payload={"order_id": "ORD_44"},
            event_timestamp=t0 + timedelta(minutes=2),
        )

    # Now attempt resurrection via ORDER_FILLED
    ledger.append(
        event_type=AuditEventType.ORDER_FILLED,
        entity_type="ORDER",
        entity_id="ORD_44",
        correlation_id="RUN_FSM44",
        causation_id=e_term.event_id,
        payload={
            "order_id": "ORD_44",
            "symbol": "NIFTY",
            "quantity": 1,
            "effective_price": "24000",
        },
        event_timestamp=t0 + timedelta(minutes=4),
    )

    source = ReplayArtifactSource(events=ledger._storage.read_all())
    res = ReplayEngine(ReplayConfig(verify_fsm_transitions=True), source).replay()
    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "FSM_TERMINAL_RESURRECTION"


# ---------------------------------------------------------------------------
# P45: Missing Mandatory Trade Attributes Strictly Fail Closed
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    missing_key=st.sampled_from(["trade_id", "symbol", "missing_pnls"]),
)
def test_p45_missing_mandatory_trade_data_fails_closed(missing_key: str) -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_P45",
        correlation_id="RUN_P45",
        causation_id="GENESIS",
        payload={"run_id": "RUN_P45", "contract_id": "NIFTY", "initial_capital": "1000000"},
        event_timestamp=t0,
    )
    sym = "UNKNOWN" if missing_key == "symbol" else "NIFTY"
    trade_id_val = "UNKNOWN" if missing_key == "trade_id" else "TRD_P45"

    e2 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_P45",
        correlation_id="RUN_P45",
        causation_id=e1.event_id,
        payload={
            "order_id": "ORD_P45",
            "symbol": sym,
            "side": "LONG",
            "quantity": 10,
            "entry_price": "24000",
        },
        event_timestamp=t0 + timedelta(minutes=1),
    )
    e3 = ledger.append(
        event_type=AuditEventType.FILL_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_P45",
        correlation_id="RUN_P45",
        causation_id=e2.event_id,
        payload={
            "order_id": "ORD_P45",
            "symbol": sym,
            "side": "LONG",
            "quantity": 10,
            "effective_price": "24000",
        },
        event_timestamp=t0 + timedelta(minutes=2),
    )

    trade_payload: dict[str, Any] = {
        "trade_id": trade_id_val,
        "symbol": sym,
        "gross_pnl": "1000",
        "net_pnl": "975",
        "exit_reason": "SIGNAL",
    }
    if missing_key == "trade_id":
        del trade_payload["trade_id"]
    elif missing_key == "missing_pnls":
        del trade_payload["gross_pnl"]
        del trade_payload["net_pnl"]

    ledger.append(
        event_type=AuditEventType.TRADE_CLOSED,
        entity_type="TRADE",
        entity_id=trade_id_val,
        correlation_id="RUN_P45",
        causation_id=e3.event_id,
        payload=trade_payload,
        event_timestamp=t0 + timedelta(minutes=3),
    )

    source = ReplayArtifactSource(events=ledger._storage.read_all())
    res = ReplayEngine(ReplayConfig(), source).replay()
    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "MISSING_TRADE_DATA"


# ---------------------------------------------------------------------------
# P46: State Fingerprint Invariance Across Intermediate Checkpoint Resumption
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    checkpoint_seq=st.integers(min_value=2, max_value=4),
)
def test_p46_checkpoint_fingerprint_invariance(checkpoint_seq: int) -> None:
    clean_ledger = _build_sample_trade_ledger()
    clean_events = clean_ledger._storage.read_all()
    assert len(clean_events) == 6

    source = ReplayArtifactSource(events=clean_events)

    # 1. Full replay without checkpoints
    full_res = ReplayEngine(ReplayConfig(mode=ReplayMode.FULL), source).replay()
    assert full_res.status == ReplayStatus.PASS
    assert full_res.final_state is not None
    full_fp = full_res.final_state_fingerprint

    # 2. Run with checkpoint interval = 1
    cp_res = ReplayEngine(
        ReplayConfig(mode=ReplayMode.FULL, checkpoint_interval=1), source
    ).replay()
    assert cp_res.status == ReplayStatus.PASS
    assert len(cp_res.checkpoints) >= checkpoint_seq

    # 3. Resume from chosen intermediate checkpoint
    target_cp = [c for c in cp_res.checkpoints if c.sequence_number == checkpoint_seq][0]
    resume_engine = ReplayEngine(ReplayConfig(mode=ReplayMode.FULL), source)
    resumed_res = resume_engine.resume_from_checkpoint(target_cp)

    assert resumed_res.status == ReplayStatus.PASS
    assert resumed_res.final_state is not None
    assert resumed_res.final_state_fingerprint == full_fp


# ---------------------------------------------------------------------------
# P47: Source Equity Mutations Cannot Alter Replay-Derived Equity
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    bogus_equity=st.integers(min_value=1, max_value=999999999),
)
def test_p47_source_equity_mutations_isolation(bogus_equity: int) -> None:
    engine, bt_res = _run_backtest_fixture(40, 15)
    events = engine.ledger._storage.read_all()

    # Clean replay baseline
    clean_res = ReplayEngine(
        ReplayConfig(), ReplayArtifactSource(events=events, backtest_result=bt_res)
    ).replay()
    assert clean_res.status == ReplayStatus.PASS
    assert clean_res.reconstructed_result is not None
    clean_curve = clean_res.reconstructed_result.equity_curve

    injected_snap = EquitySnapshot(
        timestamp=bt_res.config.start_time,
        equity=Decimal(bogus_equity),
        cash=Decimal(bogus_equity),
        margin_used=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        cumulative_fees=Decimal("0"),
        cumulative_slippage=Decimal("0"),
        notional_exposure=Decimal("0"),
        drawdown=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )
    tampered_bt_res = bt_res.model_copy(
        update={"equity_curve": bt_res.equity_curve + (injected_snap,)}
    )

    tampered_replay = ReplayEngine(
        ReplayConfig(),
        ReplayArtifactSource(events=events, backtest_result=tampered_bt_res),
    ).replay()

    assert tampered_replay.status == ReplayStatus.PASS
    assert tampered_replay.reconstructed_result is not None
    assert tampered_replay.reconstructed_result.equity_curve == clean_curve
    assert not any(
        s.equity == Decimal(bogus_equity) for s in tampered_replay.reconstructed_result.equity_curve
    )


# ---------------------------------------------------------------------------
# P48: Source Quant-Gate Mutations Cannot Alter Replay-Derived Gate State
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    gate_suffix=st.integers(min_value=100, max_value=99999),
)
def test_p48_source_quant_gates_isolation(gate_suffix: int) -> None:
    engine, bt_res = _run_backtest_fixture(40, 15)
    events = engine.ledger._storage.read_all()

    clean_res = ReplayEngine(
        ReplayConfig(), ReplayArtifactSource(events=events, backtest_result=bt_res)
    ).replay()
    assert clean_res.status == ReplayStatus.PASS
    assert clean_res.reconstructed_result is not None
    clean_gates = clean_res.reconstructed_result.quant_gates

    fake_gate_id = f"GATE_HACK_{gate_suffix}"
    fake_gate = QuantGateResult(
        gate_id=fake_gate_id,
        gate_name="HACKED_GATE",
        status=QuantGateStatus.FAIL,
        reason="Malicious injection",
        evidence={},
    )
    tampered_bt_res = bt_res.model_copy(update={"quant_gates": bt_res.quant_gates + (fake_gate,)})

    tampered_replay = ReplayEngine(
        ReplayConfig(),
        ReplayArtifactSource(events=events, backtest_result=tampered_bt_res),
    ).replay()

    assert tampered_replay.status == ReplayStatus.PASS
    assert tampered_replay.reconstructed_result is not None
    assert tampered_replay.reconstructed_result.quant_gates == clean_gates
    assert not any(
        g.gate_id == fake_gate_id for g in tampered_replay.reconstructed_result.quant_gates
    )


# ---------------------------------------------------------------------------
# P49: Source Diagnostics, Warnings, and Errors Mutations Cannot Alter Result
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    tag=st.text(min_size=3, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyz"),
)
def test_p49_source_diagnostics_warnings_errors_isolation(tag: str) -> None:
    engine, bt_res = _run_backtest_fixture(40, 15)
    events = engine.ledger._storage.read_all()

    tampered_bt_res = bt_res.model_copy(
        update={
            "diagnostics": {f"key_{tag}": f"val_{tag}"},
            "warnings": (f"warning_{tag}",),
            "errors": (f"error_{tag}",),
        }
    )

    tampered_replay = ReplayEngine(
        ReplayConfig(),
        ReplayArtifactSource(events=events, backtest_result=tampered_bt_res),
    ).replay()

    assert tampered_replay.status == ReplayStatus.PASS
    assert tampered_replay.reconstructed_result is not None
    assert tampered_replay.reconstructed_result.diagnostics == {}
    assert tampered_replay.reconstructed_result.warnings == ()
    assert tampered_replay.reconstructed_result.errors == ()


# ---------------------------------------------------------------------------
# P50: Source Non-Authoritative Trace Mutations Cannot Alter Replay Trace
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    price_noise=st.integers(min_value=50000, max_value=999999),
)
def test_p50_source_trace_mutations_isolation(price_noise: int) -> None:
    engine, bt_res = _run_backtest_fixture(40, 15)
    events = engine.ledger._storage.read_all()

    clean_res = ReplayEngine(
        ReplayConfig(verify_execution_trace=False),
        ReplayArtifactSource(events=events, backtest_result=bt_res),
    ).replay()
    assert clean_res.status == ReplayStatus.PASS
    assert clean_res.reconstructed_result is not None
    clean_trace = clean_res.reconstructed_result.execution_trace

    fake_entity = f"FORGED_ENTITY_{price_noise}"
    fake_entry = TraceEntry(
        timestamp=bt_res.config.start_time,
        event_type=TraceEventType.ORDER_SIMULATED,
        entity_id=fake_entity,
        symbol="NIFTY",
        price=Decimal(price_noise),
    )
    tampered_bt_res = bt_res.model_copy(
        update={"execution_trace": bt_res.execution_trace + (fake_entry,)}
    )

    tampered_replay = ReplayEngine(
        ReplayConfig(verify_execution_trace=False),
        ReplayArtifactSource(events=events, backtest_result=tampered_bt_res),
    ).replay()

    assert tampered_replay.status == ReplayStatus.PASS
    assert tampered_replay.reconstructed_result is not None
    assert tampered_replay.reconstructed_result.execution_trace == clean_trace
    assert not any(
        t.entity_id == fake_entity for t in tampered_replay.reconstructed_result.execution_trace
    )


# ---------------------------------------------------------------------------
# P51: Removing Authoritative Result Data Fails When Result Verification Enabled
# ---------------------------------------------------------------------------
@settings(max_examples=10, deadline=None)
@given(
    drop_index=st.integers(min_value=0, max_value=1),
)
def test_p51_missing_authoritative_data_fails_result_verification(drop_index: int) -> None:
    engine, bt_res = _run_backtest_fixture(40, 15)
    events = list(engine.ledger._storage.read_all())

    manifest = ReplayManifest(
        source_run_id=bt_res.backtest_run_id,
        source_dataset_id=bt_res.config.dataset_id,
        source_dataset_checksum="CHK_P51",
        source_strategy_id=bt_res.config.strategy_id,
        source_strategy_version=bt_res.config.strategy_version,
        source_engine_version="1.0.0",
        source_result_canonical_hash=bt_res.result_canonical_hash,
        source_trace_canonical_hash=bt_res.trace_canonical_hash,
    )

    if drop_index == 0:
        tampered_events = [e for e in events if e.event_type != AuditEventType.BACKTEST_STARTED]
    else:
        tampered_events = [e for e in events if e.event_type != AuditEventType.BACKTEST_COMPLETED]

    source = ReplayArtifactSource(
        events=tampered_events,
        manifest=manifest,
        backtest_result=bt_res,
    )

    res = ReplayEngine(
        ReplayConfig(verify_result_hash=True, verify_execution_trace=False),
        source,
    ).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
