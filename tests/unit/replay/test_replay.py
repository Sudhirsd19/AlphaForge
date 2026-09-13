"""
Unit tests for AlphaForge Replay Engine (Phase 11).
Verifies R1 through R20 requirements:
R1:  Empty replay controlled behavior
R2:  Single valid event replay
R3:  Complete valid event stream replay
R4:  Determinism (dual replay produces identical replay ID and fingerprints)
R5:  Event mutation detection (fails closed)
R6:  Hash-chain corruption detection (fails closed)
R7:  Sequence corruption detection (fails closed)
R8:  Causation corruption detection (fails closed)
R9:  FSM illegal transition detection (fails closed)
R10: Trace hash mismatch detection (fails closed)
R11: Result hash mismatch detection (fails closed)
R12: First-divergence diagnostics forensic completeness
R13: Prefix replay with deterministic cutoff
R14: Resume from checkpoint equals fresh full replay
R15: Unknown event type fails closed
R16: Incompatible schema version fails closed
R17: Deterministic replay ID from canonical manifest
R18: Source immutability preserved
R19: Contract lifecycle replay (expiry / forced close / rejected pending entries)
R20: Risk / execution event telemetry replay
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig, BacktestResult
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import (
    AuditEvent,
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


def _make_synthetic_candles(n: int = 50) -> list[MarketCandle]:
    candles = []
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    price = Decimal("24000")
    for i in range(n):
        ts = t0 + timedelta(minutes=3 * i)
        # Oscillate price around 24000
        price += Decimal("5") if i % 2 == 0 else Decimal("-3")
        c = MarketCandle(
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
        candles.append(c)
    return candles


def _run_sample_backtest() -> tuple[BacktestEngine, BacktestResult]:
    candles = _make_synthetic_candles(60)
    dataset = BacktestDataset("NIFTY_SYNTH", candles)
    t_start = candles[0].exchange_timestamp
    t_end = candles[-1].exchange_timestamp + timedelta(minutes=3)

    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t_start,
        end_time=t_end,
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, dataset)
    res = engine.run()
    return engine, res


# ---------------------------------------------------------------------------
# R1: Empty replay controlled behavior
# ---------------------------------------------------------------------------
def test_r1_empty_replay() -> None:
    source = ReplayArtifactSource(events=())
    config = ReplayConfig(mode=ReplayMode.FULL)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.WARNING
    assert res.events_processed == 0
    assert res.events_verified == 0
    assert res.replay_id.startswith("REPLAY-")
    assert len(res.replay_id) == 31  # "REPLAY-" + 24 chars
    assert len(res.warnings) >= 1
    assert len(res.errors) == 0
    assert res.first_divergence is None


# ---------------------------------------------------------------------------
# R2: Single valid event replay
# ---------------------------------------------------------------------------
def test_r2_single_valid_event() -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_SINGLE",
        correlation_id="RUN_SINGLE",
        causation_id="GENESIS",
        payload={
            "run_id": "RUN_SINGLE",
            "initial_capital": "1000000",
            "strategy_id": "TEST_STRAT",
        },
        event_timestamp=t0,
    )
    source = ReplayArtifactSource(ledger=ledger)
    config = ReplayConfig(mode=ReplayMode.FULL)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.PASS
    assert res.events_processed == 1
    assert res.events_verified == 1
    assert res.final_state is not None
    assert res.final_state.portfolio.cash == Decimal("1000000")
    assert res.first_divergence is None


# ---------------------------------------------------------------------------
# R3: Complete valid event stream replay
# ---------------------------------------------------------------------------
def test_r3_complete_valid_event_stream() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger.get_events_by_correlation_id(bt_res.backtest_run_id) or tuple(
        bt_engine.ledger._storage.read_all()
    )

    source = ReplayArtifactSource(events=events, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL)
    engine = ReplayEngine(config, source)
    res = engine.replay()

    assert res.status == ReplayStatus.PASS
    assert res.events_processed == len(events)
    assert res.events_verified == len(events)
    assert res.first_divergence is None
    assert len(res.errors) == 0
    assert res.final_state is not None


# ---------------------------------------------------------------------------
# R4: Determinism (dual replay produces identical ID and fingerprints)
# ---------------------------------------------------------------------------
def test_r4_dual_replay_determinism() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger._storage.read_all()

    source1 = ReplayArtifactSource(events=events, backtest_result=bt_res)
    source2 = ReplayArtifactSource(events=events, backtest_result=bt_res)

    res1 = ReplayEngine(ReplayConfig(), source1).replay()
    res2 = ReplayEngine(ReplayConfig(), source2).replay()

    assert res1.replay_id == res2.replay_id
    assert res1.final_state_fingerprint == res2.final_state_fingerprint
    assert res1.replay_trace_hash == res2.replay_trace_hash
    assert res1.status == res2.status
    assert res1.events_verified == res2.events_verified


# ---------------------------------------------------------------------------
# R5: Event mutation detection (fails closed)
# ---------------------------------------------------------------------------
def test_r5_event_mutation_detection() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = list(bt_engine.ledger._storage.read_all())
    assert len(events) >= 2

    # Mutate payload of second event without updating event_id or event_hash
    target_idx = 1
    mutated_payload = dict(events[target_idx].payload)
    mutated_payload["tampered_key"] = "tampered_value"

    # Bypass pydantic validation via object.__setattr__ to simulate disk tampering
    tampered_event = events[target_idx].model_copy()
    object.__setattr__(tampered_event, "payload", mutated_payload)
    events[target_idx] = tampered_event

    source = ReplayArtifactSource(events=events)
    engine = ReplayEngine(ReplayConfig(), source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category in (
        "EVENT_ID_CORRUPTION",
        "EVENT_HASH_CORRUPTION",
    )


# ---------------------------------------------------------------------------
# R6: Hash-chain corruption detection
# ---------------------------------------------------------------------------
def test_r6_hash_chain_corruption() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = list(bt_engine.ledger._storage.read_all())
    assert len(events) >= 3

    # Corrupt previous_event_hash of event 2
    tampered_event = events[2].model_copy()
    object.__setattr__(tampered_event, "previous_event_hash", "0" * 64)
    events[2] = tampered_event

    source = ReplayArtifactSource(events=events)
    engine = ReplayEngine(ReplayConfig(), source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.divergent_sequence == events[2].sequence_number
    assert res.first_divergence.mismatch_category == "HASH_CHAIN_DIVERGENCE"


# ---------------------------------------------------------------------------
# R7: Sequence corruption detection
# ---------------------------------------------------------------------------
def test_r7_sequence_corruption() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = list(bt_engine.ledger._storage.read_all())
    assert len(events) >= 3

    # Corrupt sequence number of event 1 to 99
    tampered_event = events[1].model_copy()
    object.__setattr__(tampered_event, "sequence_number", 99)
    events[1] = tampered_event

    source = ReplayArtifactSource(events=events)
    engine = ReplayEngine(ReplayConfig(), source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "SEQUENCE_DISCONTINUITY"


# ---------------------------------------------------------------------------
# R8: Causation corruption detection
# ---------------------------------------------------------------------------
def test_r8_causation_corruption() -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_C",
        correlation_id="RUN_C",
        causation_id="GENESIS",
        payload={"run_id": "RUN_C"},
        event_timestamp=t0,
    )
    # Event 2 has an unknown, fake causation reference
    ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_1",
        correlation_id="RUN_C",
        causation_id="UNKNOWN_CAUSAL_PARENT_XYZ",
        payload={"order_id": "ORD_1", "quantity": 1, "side": "LONG"},
        event_timestamp=t0 + timedelta(minutes=3),
    )
    events = ledger._storage.read_all()
    source = ReplayArtifactSource(events=events)
    engine = ReplayEngine(ReplayConfig(), source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "BROKEN_CAUSAL_LINEAGE"


# ---------------------------------------------------------------------------
# R9: FSM illegal transition detection
# ---------------------------------------------------------------------------
def test_r9_fsm_illegal_transition() -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.ORDER_SIMULATED,
        entity_type="ORDER",
        entity_id="ORD_FSM",
        correlation_id="RUN_FSM",
        causation_id="GENESIS",
        payload={"order_id": "ORD_FSM", "quantity": 1, "side": "LONG"},
        event_timestamp=t0,
    )
    # Reject the order -> transitions to terminal REJECTED state
    e2 = ledger.append(
        event_type=AuditEventType.ORDER_REJECTED,
        entity_type="ORDER",
        entity_id="ORD_FSM",
        correlation_id="RUN_FSM",
        causation_id=e1.event_id,
        payload={"order_id": "ORD_FSM"},
        event_timestamp=t0 + timedelta(minutes=3),
    )
    # Attempt to transition from terminal REJECTED to VALIDATED -> illegal!
    ledger.append(
        event_type=AuditEventType.ORDER_VALIDATED,
        entity_type="ORDER",
        entity_id="ORD_FSM",
        correlation_id="RUN_FSM",
        causation_id=e2.event_id,
        payload={"order_id": "ORD_FSM"},
        event_timestamp=t0 + timedelta(minutes=6),
    )
    source = ReplayArtifactSource(events=ledger._storage.read_all())
    engine = ReplayEngine(ReplayConfig(verify_fsm_transitions=True), source)
    res = engine.replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "FSM_TERMINAL_RESURRECTION"


# ---------------------------------------------------------------------------
# R10: Trace hash mismatch detection
# ---------------------------------------------------------------------------
def test_r10_trace_hash_mismatch() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger._storage.read_all()

    manifest = ReplayManifest(
        source_run_id=bt_res.backtest_run_id,
        source_dataset_id="DS",
        source_dataset_checksum="CHK",
        source_strategy_id="STRAT",
        source_strategy_version="1.0.0",
        source_engine_version="1.0.0",
        source_trace_canonical_hash="f" * 64,  # Intentionally false trace hash
    )
    source = ReplayArtifactSource(events=events, manifest=manifest)
    res = ReplayEngine(ReplayConfig(verify_execution_trace=True), source).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "TRACE_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# R11: Result hash mismatch detection
# ---------------------------------------------------------------------------
def test_r11_result_hash_mismatch() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger._storage.read_all()

    manifest = ReplayManifest(
        source_run_id=bt_res.backtest_run_id,
        source_dataset_id="DS",
        source_dataset_checksum="CHK",
        source_strategy_id="STRAT",
        source_strategy_version="1.0.0",
        source_engine_version="1.0.0",
        source_result_canonical_hash="e" * 64,  # Intentionally false result hash
    )
    source = ReplayArtifactSource(events=events, backtest_result=bt_res, manifest=manifest)
    res = ReplayEngine(ReplayConfig(verify_result_hash=True), source).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "RESULT_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# R12: First-divergence diagnostics forensic completeness
# ---------------------------------------------------------------------------
def test_r12_first_divergence_diagnostics_completeness() -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_DIV",
        correlation_id="RUN_DIV",
        causation_id="GENESIS",
        payload={"run_id": "RUN_DIV"},
        event_timestamp=t0,
    )
    events = list(ledger._storage.read_all())
    # Tamper previous_event_hash to trigger divergence
    tampered = events[0].model_copy()
    object.__setattr__(tampered, "previous_event_hash", "WRONG_GENESIS")
    events[0] = tampered

    source = ReplayArtifactSource(events=events)
    res = ReplayEngine(ReplayConfig(), source).replay()

    assert res.status == ReplayStatus.FAIL
    div = res.first_divergence
    assert div is not None
    assert div.divergent_sequence == 1
    assert div.event_id == events[0].event_id
    assert div.event_type == AuditEventType.BACKTEST_STARTED.value
    assert len(div.source_fingerprint) > 0
    assert len(div.replay_fingerprint) > 0
    assert len(div.diagnostic) > 0
    assert div.mismatch_category == "HASH_CHAIN_DIVERGENCE"


# ---------------------------------------------------------------------------
# R13: Prefix replay with deterministic cutoff
# ---------------------------------------------------------------------------
def test_r13_prefix_replay() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger._storage.read_all()
    assert len(events) >= 5

    cutoff_seq = 3
    source = ReplayArtifactSource(events=events)
    config = ReplayConfig(mode=ReplayMode.PREFIX, prefix_cutoff_sequence=cutoff_seq)
    res = ReplayEngine(config, source).replay()

    assert res.status == ReplayStatus.PASS
    assert res.events_processed == cutoff_seq
    assert res.events_verified == cutoff_seq
    assert res.final_state is not None
    assert res.final_state.last_processed_sequence == cutoff_seq


# ---------------------------------------------------------------------------
# R14: Resume from checkpoint equals fresh full replay
# ---------------------------------------------------------------------------
def test_r14_resume_from_checkpoint() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events = bt_engine.ledger._storage.read_all()
    assert len(events) >= 5

    # 1. Run full fresh replay with frequent checkpoints
    source = ReplayArtifactSource(events=events, backtest_result=bt_res)
    config = ReplayConfig(mode=ReplayMode.FULL, checkpoint_interval=2)
    full_engine = ReplayEngine(config, source)
    fresh_res = full_engine.replay()
    assert fresh_res.status == ReplayStatus.PASS
    assert len(fresh_res.checkpoints) >= 2

    # 2. Pick intermediate checkpoint
    mid_checkpoint = fresh_res.checkpoints[1]

    # 3. Resume replay from checkpoint
    resume_engine = ReplayEngine(config, source)
    resumed_res = resume_engine.resume_from_checkpoint(mid_checkpoint)

    # 4. Compare exact equivalence
    assert resumed_res.status == ReplayStatus.PASS
    assert resumed_res.final_state_fingerprint == fresh_res.final_state_fingerprint
    assert resumed_res.events_verified == fresh_res.events_verified
    assert resumed_res.replay_id == fresh_res.replay_id


# ---------------------------------------------------------------------------
# R15: Unknown event type fails closed
# ---------------------------------------------------------------------------
def test_r15_unknown_event_type_fails_closed() -> None:
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    ev_id = "EVT-UNKNOWN-TYPE"
    # Construct an event with unparseable event_type bypass
    event = AuditEvent.model_construct(
        event_id=ev_id,
        sequence_number=1,
        event_timestamp=t0,
        event_type="UNSUPPORTED_ALIEN_EVENT",
        entity_type="SYSTEM",
        entity_id="SYS_1",
        correlation_id="RUN_UNKNOWN",
        causation_id="GENESIS",
        payload={"k": "v"},
        previous_event_hash="GENESIS",
        event_hash="a" * 64,
        schema_version=1,
    )
    source = ReplayArtifactSource(events=[event])
    config = ReplayConfig(verify_audit_hash_chain=False)  # Focus on event type validation
    res = ReplayEngine(config, source).replay()

    assert res.status == ReplayStatus.FAIL
    assert res.first_divergence is not None
    assert res.first_divergence.mismatch_category == "UNKNOWN_EVENT_TYPE"


# ---------------------------------------------------------------------------
# R16: Incompatible schema version fails closed
# ---------------------------------------------------------------------------
def test_r16_schema_incompatibility_fails_closed() -> None:
    manifest = ReplayManifest(
        source_run_id="RUN_SCH",
        source_dataset_id="DS",
        source_dataset_checksum="CHK",
        source_strategy_id="STRAT",
        source_strategy_version="1.0.0",
        source_engine_version="1.0.0",
        source_replay_schema_version="999.0.0",  # Incompatible future schema
    )
    with pytest.raises(Exception) as exc_info:
        ReplayArtifactSource(events=(), manifest=manifest)
    assert "Incompatible replay schema version" in str(exc_info.value)


# ---------------------------------------------------------------------------
# R17: Deterministic replay ID from canonical manifest
# ---------------------------------------------------------------------------
def test_r17_deterministic_replay_id() -> None:
    manifest1 = ReplayManifest(
        source_run_id="RUN_DET",
        source_dataset_id="DS_1",
        source_dataset_checksum="CHK_1",
        source_strategy_id="STRAT_A",
        source_strategy_version="1.0.0",
        source_engine_version="1.0.0",
    )
    manifest2 = ReplayManifest(
        source_run_id="RUN_DET",
        source_dataset_id="DS_1",
        source_dataset_checksum="CHK_1",
        source_strategy_id="STRAT_A",
        source_strategy_version="1.0.0",
        source_engine_version="1.0.0",
    )
    id1 = manifest1.compute_replay_id()
    id2 = manifest2.compute_replay_id()
    assert id1 == id2
    assert id1.startswith("REPLAY-")


# ---------------------------------------------------------------------------
# R18: Source immutability preserved
# ---------------------------------------------------------------------------
def test_r18_source_immutability() -> None:
    bt_engine, bt_res = _run_sample_backtest()
    events_before = [e.model_copy() for e in bt_engine.ledger._storage.read_all()]

    source = ReplayArtifactSource(ledger=bt_engine.ledger, backtest_result=bt_res)
    ReplayEngine(ReplayConfig(), source).replay()

    events_after = bt_engine.ledger._storage.read_all()
    assert len(events_before) == len(events_after)
    for eb, ea in zip(events_before, events_after, strict=True):
        assert eb.event_id == ea.event_id
        assert eb.event_hash == ea.event_hash
        assert eb.sequence_number == ea.sequence_number
        assert eb.payload == ea.payload


# ---------------------------------------------------------------------------
# R19: Contract lifecycle replay (expiry / forced close / rejected pending entries)
# ---------------------------------------------------------------------------
def test_r19_contract_lifecycle_replay() -> None:
    candles = _make_synthetic_candles(40)
    dataset = BacktestDataset("NIFTY_EXPIRY_DS", candles)
    t0 = candles[0].exchange_timestamp
    expiry_ts = t0 + timedelta(minutes=30)

    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY-EXP",
        expiry_datetime=expiry_ts,
        listing_datetime=t0 - timedelta(days=90),
        trading_start_datetime=t0 - timedelta(days=90),
        trading_end_datetime=expiry_ts,
        lot_size=1,
        tick_size=Decimal("0.05"),
        data_source="NSE",
    )
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id=dataset.dataset_id,
        start_time=t0,
        end_time=candles[-1].exchange_timestamp,
        initial_capital=Decimal("1000000"),
        warmup_bars=5,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, dataset, contract_master=contract)
    res = engine.run()

    source = ReplayArtifactSource(events=engine.ledger._storage.read_all(), backtest_result=res)
    replay_res = ReplayEngine(ReplayConfig(), source).replay()

    assert replay_res.status == ReplayStatus.PASS
    assert replay_res.final_state is not None
    assert replay_res.final_state.contract.post_expiry_fills == 0
    assert replay_res.final_state.contract.lifecycle_violation is False


# ---------------------------------------------------------------------------
# R20: Risk / execution event telemetry replay
# ---------------------------------------------------------------------------
def test_r20_risk_execution_telemetry_replay() -> None:
    ledger = AuditLedger(InMemoryLedgerStorage())
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    e1 = ledger.append(
        event_type=AuditEventType.BACKTEST_STARTED,
        entity_type="BACKTEST",
        entity_id="RUN_RISK",
        correlation_id="RUN_RISK",
        causation_id="GENESIS",
        payload={"run_id": "RUN_RISK"},
        event_timestamp=t0,
    )
    e2 = ledger.append(
        event_type=AuditEventType.RISK_CHECK,
        entity_type="SIGNAL",
        entity_id="SIG_1",
        correlation_id="RUN_RISK",
        causation_id=e1.event_id,
        payload={"signal_id": "SIG_1"},
        event_timestamp=t0 + timedelta(minutes=1),
    )
    e3 = ledger.append(
        event_type=AuditEventType.RISK_REJECTED,
        entity_type="SIGNAL",
        entity_id="SIG_2",
        correlation_id="RUN_RISK",
        causation_id=e2.event_id,
        payload={"signal_id": "SIG_2", "reason": "MAX_RISK_EXCEEDED"},
        event_timestamp=t0 + timedelta(minutes=2),
    )
    ledger.append(
        event_type=AuditEventType.CIRCUIT_BREAKER_TRIGGERED,
        entity_type="RISK",
        entity_id="CIRCUIT_1",
        correlation_id="RUN_RISK",
        causation_id=e3.event_id,
        payload={"reason": "DRAWDOWN_LIMIT"},
        event_timestamp=t0 + timedelta(minutes=3),
    )
    source = ReplayArtifactSource(events=ledger._storage.read_all())
    res = ReplayEngine(ReplayConfig(), source).replay()

    assert res.status == ReplayStatus.PASS
    assert res.final_state is not None
    assert res.final_state.risk.risk_evaluations == 2
    assert res.final_state.risk.risk_approvals == 1
    assert res.final_state.risk.risk_rejections == 1
    assert res.final_state.risk.circuit_breaker_active is True
