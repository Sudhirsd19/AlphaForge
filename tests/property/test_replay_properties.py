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
"""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig, BacktestResult
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
