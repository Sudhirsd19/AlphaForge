"""
Forensic regression test suite for AlphaForge Phase 1 remediation.
Covers:
- Closed candle quarantine and index [0] isolation
- Higher-timeframe confirmation candle sorting and tie-breaking (AF-P2-01)
- Structural swing lookback configurability and config hash sensitivity (AF-P2-02)
- Directional SL & Target orientation
- Fixed 1:2 Reward-to-Risk exactness
- Risk distance guardrails (0.10% - 3.00%)
- Stale data threshold (195s)
- Mathematical determinism across repeated executions
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.core.models import Candle
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine

FIXTURES_DIR = Path(__file__).parents[2] / "golden" / "fixtures"


def _load_fixture(filename: str) -> dict[str, Any]:
    file_path = FIXTURES_DIR / filename
    with file_path.open(encoding="utf-8") as f:
        return cast("dict[str, Any]", json.load(f))


def _deserialize_candle(d: dict[str, Any]) -> Candle:
    return Candle(
        timestamp=datetime.fromisoformat(d["timestamp"]),
        open=Decimal(d["open"]),
        high=Decimal(d["high"]),
        low=Decimal(d["low"]),
        close=Decimal(d["close"]),
        volume=int(d["volume"]),
        open_interest=d["open_interest"],
        is_closed=bool(d["is_closed"]),
    )


def test_closed_candle_quarantine_mutation() -> None:
    """Proves that mutating forming candle [0] produces zero change in output."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine_base = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    baseline_signal = engine_base.evaluate(exec_c, conf_c, status, eval_time)
    assert baseline_signal.decision == StrategyDecision.ACCEPT

    # Mutate index [0] with wild prices and volume
    mutated_exec = list(exec_c)
    mutated_exec[0] = Candle(
        timestamp=exec_c[0].timestamp,
        open=Decimal("99999.00"),
        high=Decimal("150000.00"),
        low=Decimal("1000.00"),
        close=Decimal("120000.00"),
        volume=9999999,
        is_closed=False,
    )

    engine_mut = DeterministicStrategyEngine()
    mutated_signal = engine_mut.evaluate(mutated_exec, conf_c, status, eval_time)
    assert mutated_signal.signal_id == baseline_signal.signal_id
    assert mutated_signal.decision == baseline_signal.decision
    assert mutated_signal.entry_reference == baseline_signal.entry_reference
    assert mutated_signal.stop_reference == baseline_signal.stop_reference
    assert mutated_signal.target_reference == baseline_signal.target_reference
    assert mutated_signal.risk_distance == baseline_signal.risk_distance


def test_confirmation_ordering_invariance() -> None:
    """
    Proves that reversed and shuffled confirmation inputs produce identical results (AF-P2-01).
    """
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    # Normal input
    sig_normal = DeterministicStrategyEngine().evaluate(exec_c, conf_c, status, eval_time)
    assert sig_normal.decision == StrategyDecision.ACCEPT

    # Reversed confirmation input
    reversed_conf = list(reversed(conf_c))
    sig_reversed = DeterministicStrategyEngine().evaluate(exec_c, reversed_conf, status, eval_time)
    assert sig_reversed.decision == StrategyDecision.ACCEPT
    assert sig_reversed.signal_id == sig_normal.signal_id
    assert sig_reversed.entry_reference == sig_normal.entry_reference
    assert sig_reversed.stop_reference == sig_normal.stop_reference
    assert sig_reversed.target_reference == sig_normal.target_reference

    # Shuffled confirmation input (interleaved)
    shuffled_conf = conf_c[::2] + conf_c[1::2]
    sig_shuffled = DeterministicStrategyEngine().evaluate(exec_c, shuffled_conf, status, eval_time)
    assert sig_shuffled.signal_id == sig_normal.signal_id


def test_confirmation_duplicate_timestamp_handling() -> None:
    """Proves duplicate timestamps in confirmation candles are handled deterministically."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    # Duplicate middle candle with identical timestamp
    dup_conf = list(conf_c)
    dup_candle = Candle(
        timestamp=conf_c[10].timestamp,
        open=conf_c[10].open,
        high=conf_c[10].high,
        low=conf_c[10].low,
        close=conf_c[10].close,
        volume=conf_c[10].volume + 500,
        is_closed=True,
    )
    dup_conf.insert(10, dup_candle)

    sig = engine.evaluate(exec_c, dup_conf, status, eval_time)
    assert sig.decision == StrategyDecision.ACCEPT


def test_config_hash_sensitivity_all_parameters() -> None:
    """Proves every strategy-affecting configuration parameter modifies config_hash (AF-P2-02)."""
    base = StrategyConfig()
    base_hash = base.compute_config_hash()

    # Identical config produces bit-exact identical hash
    assert StrategyConfig().compute_config_hash() == base_hash

    mutations = [
        ("swing_stop_lookback", 3),
        ("trend_ema_fast", 10),
        ("trend_ema_slow", 22),
        ("breakout_lookback", 21),
        ("min_body_ratio", Decimal("0.55")),
        ("min_close_location_ratio", Decimal("0.75")),
        ("volume_lookback", 25),
        ("min_relative_volume", Decimal("1.30")),
        ("rsi_period", 15),
        ("rsi_long_min", Decimal("52.0")),
        ("rsi_long_max", Decimal("78.0")),
        ("rsi_short_min", Decimal("22.0")),
        ("rsi_short_max", Decimal("48.0")),
        ("atr_period", 15),
        ("atr_stop_multiplier", Decimal("1.5")),
        ("atr_min_pct", Decimal("0.0010")),
        ("atr_max_pct", Decimal("0.0200")),
        ("min_risk_distance_pct", Decimal("0.0020")),
        ("max_risk_distance_pct", Decimal("0.0400")),
        ("target_risk_multiple", Decimal("2.5")),
        ("max_stale_seconds", 200),
        ("enable_regime_filter", True),
        ("min_adx_threshold", Decimal("25.0")),
        ("min_ema_spread_pct", Decimal("0.0012")),
    ]

    for param, new_val in mutations:
        kwargs: dict[str, Any] = {param: new_val}
        mutated_cfg = cast("Any", StrategyConfig)(**kwargs)
        mutated_hash = mutated_cfg.compute_config_hash()
        assert mutated_hash != base_hash, f"Parameter '{param}' mutation did not alter config_hash!"


def test_swing_stop_lookback_functional_effect() -> None:
    """Proves stop calculation actually consumes swing_stop_lookback (AF-P2-02)."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    # Modify candle [3] (index 3 of closed_exec, i.e., index 4 of raw exec_candles)
    # Give it a lower low that will be included ONLY when lookback >= 4
    low_modified_exec = list(exec_c)
    low_modified_exec[4] = Candle(
        timestamp=exec_c[4].timestamp,
        open=exec_c[4].open,
        high=exec_c[4].high,
        low=exec_c[4].low - Decimal("20.00"),
        close=exec_c[4].close,
        volume=exec_c[4].volume,
        is_closed=True,
    )

    # Evaluate with lookback=2 (should ignore index 4)
    engine_lb2 = DeterministicStrategyEngine(StrategyConfig(swing_stop_lookback=2))
    sig_lb2 = engine_lb2.evaluate(low_modified_exec, conf_c, status, eval_time)

    # Evaluate with lookback=4 (should include index 4)
    engine_lb4 = DeterministicStrategyEngine(StrategyConfig(swing_stop_lookback=4))
    sig_lb4 = engine_lb4.evaluate(low_modified_exec, conf_c, status, eval_time)

    assert sig_lb2.decision == StrategyDecision.ACCEPT
    assert sig_lb4.decision == StrategyDecision.ACCEPT
    # Lookback 4 should have a lower stop (larger risk distance) than lookback 2
    assert sig_lb4.stop_reference < sig_lb2.stop_reference
    assert sig_lb4.risk_distance > sig_lb2.risk_distance


def test_directional_sl_and_target_orientation() -> None:
    """Verify long and short stops/targets are strictly on the correct sides of entry."""
    # Long scenario
    data_long = _load_fixture("scenario_01_valid_long.json")
    exec_long = [_deserialize_candle(c) for c in data_long["exec_candles"]]
    conf_long = [_deserialize_candle(c) for c in data_long["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_long = datetime.fromisoformat(data_long["evaluation_timestamp"])

    sig_long = engine.evaluate(exec_long, conf_long, FuturesConfirmationStatus.CONFIRMED, eval_long)
    assert sig_long.direction == SignalDirection.LONG
    assert sig_long.stop_reference < sig_long.entry_reference
    assert sig_long.target_reference > sig_long.entry_reference

    # Short scenario
    data_short = _load_fixture("scenario_02_valid_short.json")
    exec_short = [_deserialize_candle(c) for c in data_short["exec_candles"]]
    conf_short = [_deserialize_candle(c) for c in data_short["conf_candles"]]
    eval_short = datetime.fromisoformat(data_short["evaluation_timestamp"])

    sig_short = engine.evaluate(
        exec_short, conf_short, FuturesConfirmationStatus.CONFIRMED, eval_short
    )
    assert sig_short.direction == SignalDirection.SHORT
    assert sig_short.stop_reference > sig_short.entry_reference
    assert sig_short.target_reference < sig_short.entry_reference


def test_reward_to_risk_exactness() -> None:
    """Verify target distance is exactly 2.0x risk distance for both LONG and SHORT."""
    engine = DeterministicStrategyEngine()

    # LONG
    data_l = _load_fixture("scenario_01_valid_long.json")
    exec_l = [_deserialize_candle(c) for c in data_l["exec_candles"]]
    conf_l = [_deserialize_candle(c) for c in data_l["conf_candles"]]
    sig_l = engine.evaluate(
        exec_l,
        conf_l,
        FuturesConfirmationStatus.CONFIRMED,
        datetime.fromisoformat(data_l["evaluation_timestamp"]),
    )
    target_dist_l = sig_l.target_reference - sig_l.entry_reference
    expected_target_dist_l = sig_l.risk_distance * Decimal("2.0")
    assert target_dist_l == expected_target_dist_l

    # SHORT
    data_s = _load_fixture("scenario_02_valid_short.json")
    exec_s = [_deserialize_candle(c) for c in data_s["exec_candles"]]
    conf_s = [_deserialize_candle(c) for c in data_s["conf_candles"]]
    sig_s = engine.evaluate(
        exec_s,
        conf_s,
        FuturesConfirmationStatus.CONFIRMED,
        datetime.fromisoformat(data_s["evaluation_timestamp"]),
    )
    target_dist_s = sig_s.entry_reference - sig_s.target_reference
    expected_target_dist_s = sig_s.risk_distance * Decimal("2.0")
    assert target_dist_s == expected_target_dist_s


def test_risk_distance_boundary_rejection() -> None:
    """Verify risk distance outside 0.10% - 3.00% is rejected with REJECT_INVALID_STOP."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus.CONFIRMED

    # Too high minimum bound (> risk distance)
    cfg_high_min = StrategyConfig(min_risk_distance_pct=Decimal("0.50"))
    engine_high = DeterministicStrategyEngine(cfg_high_min)
    sig_high = engine_high.evaluate(exec_c, conf_c, status, eval_time)
    assert sig_high.decision == StrategyDecision.REJECT
    assert sig_high.rejection_code == RejectionCode.REJECT_INVALID_STOP

    # Too low maximum bound (< risk distance)
    cfg_low_max = StrategyConfig(max_risk_distance_pct=Decimal("0.0001"))
    engine_low = DeterministicStrategyEngine(cfg_low_max)
    sig_low = engine_low.evaluate(exec_c, conf_c, status, eval_time)
    assert sig_low.decision == StrategyDecision.REJECT
    assert sig_low.rejection_code == RejectionCode.REJECT_INVALID_STOP


def test_stale_data_threshold_rejection() -> None:
    """Verify data <= 195 seconds is accepted; > 195 seconds is rejected as REJECT_DATA_STALE."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    trigger_time = exec_c[1].timestamp
    status = FuturesConfirmationStatus.CONFIRMED

    # Exactly 195 seconds late -> Accepted
    eval_fresh = trigger_time + timedelta(seconds=195)
    sig_fresh = engine.evaluate(exec_c, conf_c, status, eval_fresh)
    assert sig_fresh.decision == StrategyDecision.ACCEPT

    # 196 seconds late -> Rejected as STALE
    eval_stale = trigger_time + timedelta(seconds=196)
    sig_stale = engine.evaluate(exec_c, conf_c, status, eval_stale)
    assert sig_stale.decision == StrategyDecision.REJECT
    assert sig_stale.rejection_code == RejectionCode.REJECT_DATA_STALE


def test_mathematical_determinism_100_runs() -> None:
    """Verify same input and configuration produce bit-exact identical output across 100 runs."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus.CONFIRMED

    first_sig = None
    for _ in range(100):
        # Create fresh engine instance each run to verify zero state leakage
        engine = DeterministicStrategyEngine()
        sig = engine.evaluate(exec_c, conf_c, status, eval_time)
        if first_sig is None:
            first_sig = sig
        else:
            assert sig.signal_id == first_sig.signal_id
            assert sig.entry_reference == first_sig.entry_reference
            assert sig.stop_reference == first_sig.stop_reference
            assert sig.target_reference == first_sig.target_reference
            assert sig.risk_distance == first_sig.risk_distance
            assert sig.decision == first_sig.decision
            assert sig.config_hash == first_sig.config_hash
