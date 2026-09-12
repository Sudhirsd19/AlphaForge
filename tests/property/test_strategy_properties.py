"""
Property-based tests for AlphaForge Strategy Engine using Hypothesis.
Verifies invariants across randomized inputs.
"""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from alphaforge.core.enums import FuturesConfirmationStatus
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import create_candle, generate_candle_series


@given(
    st.integers(min_value=10000, max_value=50000),
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=1000000),
)
def test_property_forming_candle_has_zero_influence(
    price_int: int, offset: int, volume: int
) -> None:
    """
    Hypothesis property test:
    Randomly fuzzing the forming candle [0] must NEVER alter the StrategySignal decision.
    """
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(
        base_time, count=30, interval_minutes=15, trend_type="bullish"
    )

    engine = DeterministicStrategyEngine()
    eval_time = exec_candles[1].timestamp

    baseline = engine.evaluate(
        exec_candles, conf_candles, FuturesConfirmationStatus.CONFIRMED, eval_time
    )

    # Construct randomized forming candle
    f_open = Decimal(price_int)
    f_high = f_open + Decimal(offset)
    f_low = f_open - Decimal(offset)
    f_close = f_open + Decimal(offset // 2)

    random_forming = create_candle(
        timestamp=exec_candles[0].timestamp,
        open_price=f_open,
        high_price=f_high,
        low_price=f_low,
        close_price=f_close,
        volume=volume,
        is_closed=False,
    )

    fuzzed_series = [random_forming] + exec_candles[1:]
    fuzzed_signal = engine.evaluate(
        fuzzed_series, conf_candles, FuturesConfirmationStatus.CONFIRMED, eval_time
    )

    assert baseline.signal_id == fuzzed_signal.signal_id
    assert baseline.decision == fuzzed_signal.decision
    assert baseline.entry_reference == fuzzed_signal.entry_reference
    assert baseline.stop_reference == fuzzed_signal.stop_reference
    assert baseline.target_reference == fuzzed_signal.target_reference
    assert baseline.risk_distance == fuzzed_signal.risk_distance


@given(st.integers(min_value=10, max_value=40), st.integers(min_value=5, max_value=25))
def test_property_config_hash_stability(breakout_lookback: int, volume_lookback: int) -> None:
    """Config hash is deterministic and collision-resistant across randomized parameters."""
    cfg_a = StrategyConfig(breakout_lookback=breakout_lookback, volume_lookback=volume_lookback)
    cfg_b = StrategyConfig(breakout_lookback=breakout_lookback, volume_lookback=volume_lookback)

    assert cfg_a.compute_config_hash() == cfg_b.compute_config_hash()
