"""
Unit tests for deterministic strategy behavior.
Verifies that identical inputs produce bit-exact identical decisions and hashes.
"""

from datetime import datetime, timezone
from decimal import Decimal

from alphaforge.core.enums import FuturesConfirmationStatus, StrategyDecision
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import generate_candle_series


def test_strategy_is_deterministic_across_repeated_evaluations() -> None:
    """Evaluate identical inputs 100 times; all 100 outputs must be bit-exact identical."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")

    engine = DeterministicStrategyEngine()
    eval_time = exec_candles[1].timestamp

    first_signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    for _ in range(99):
        # Create fresh engine instance to guarantee no state retention
        fresh_engine = DeterministicStrategyEngine()
        subsequent_signal = fresh_engine.evaluate(
            raw_exec_candles=exec_candles,
            raw_conf_candles=conf_candles,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=eval_time,
        )
        assert first_signal.signal_id == subsequent_signal.signal_id
        assert first_signal.decision == subsequent_signal.decision
        assert first_signal.rejection_code == subsequent_signal.rejection_code
        assert first_signal.entry_reference == subsequent_signal.entry_reference
        assert first_signal.stop_reference == subsequent_signal.stop_reference
        assert first_signal.target_reference == subsequent_signal.target_reference
        assert first_signal.risk_distance == subsequent_signal.risk_distance
        assert first_signal.config_hash == subsequent_signal.config_hash


def test_config_hash_changes_deterministically() -> None:
    """Modifying any config parameter produces a distinct, reproducible hash."""
    cfg1 = StrategyConfig()
    cfg2 = StrategyConfig(breakout_lookback=25)
    cfg3 = StrategyConfig(breakout_lookback=20)  # Same as default

    hash1 = cfg1.compute_config_hash()
    hash2 = cfg2.compute_config_hash()
    hash3 = cfg3.compute_config_hash()

    assert hash1 != hash2
    assert hash1 == hash3
    assert len(hash1) == 64  # SHA-256 hex digest length
