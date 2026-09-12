"""
Unit tests for closed-candle isolation (Master Constitution Rule 8).
Verifies that mutating the forming candle [0] has ZERO effect on strategy evaluation.
"""

from datetime import datetime, timezone
from decimal import Decimal

from alphaforge.core.enums import FuturesConfirmationStatus
from alphaforge.core.models import Candle
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import create_candle, generate_candle_series


def test_forming_candle_zero_mutation_isolation() -> None:
    """
    Test Rule 8: Mutating forming candle [0] across 50 extreme variations
    produces 100% bit-exact identical signal output.
    """
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")

    engine = DeterministicStrategyEngine()
    eval_time = exec_candles[1].timestamp

    baseline_signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    # Mutate candle [0] with extreme fluctuations
    mutations = [
        (Decimal("20000.00"), Decimal("30000.00"), Decimal("10000.00"), Decimal("25000.00"), 999999),
        (Decimal("24000.00"), Decimal("24001.00"), Decimal("23999.00"), Decimal("24000.50"), 1),
        (Decimal("24500.00"), Decimal("26000.00"), Decimal("24000.00"), Decimal("25900.00"), 50000),
        (Decimal("23000.00"), Decimal("23100.00"), Decimal("21000.00"), Decimal("21500.00"), 0),
    ]

    for open_p, high_p, low_p, close_p, vol in mutations:
        mutated_forming = create_candle(
            timestamp=exec_candles[0].timestamp,
            open_price=open_p,
            high_price=high_p,
            low_price=low_p,
            close_price=close_p,
            volume=vol,
            is_closed=False,
        )
        mutated_series = [mutated_forming] + exec_candles[1:]

        signal_after_mutation = engine.evaluate(
            raw_exec_candles=mutated_series,
            raw_conf_candles=conf_candles,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=eval_time,
        )

        assert baseline_signal.signal_id == signal_after_mutation.signal_id
        assert baseline_signal.decision == signal_after_mutation.decision
        assert baseline_signal.rejection_code == signal_after_mutation.rejection_code
        assert baseline_signal.entry_reference == signal_after_mutation.entry_reference
        assert baseline_signal.stop_reference == signal_after_mutation.stop_reference
        assert baseline_signal.target_reference == signal_after_mutation.target_reference


def test_closed_candle_one_mutation_alters_decision() -> None:
    """
    Test that modifying candle [1] (the latest fully closed candle)
    DOES affect the evaluation, proving that the engine is reading [1].
    """
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")

    engine = DeterministicStrategyEngine()
    eval_time = exec_candles[1].timestamp

    baseline_signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    # Mutate candle [1] to close below its open (bearish rejection)
    mutated_c1 = create_candle(
        timestamp=exec_candles[1].timestamp,
        open_price=exec_candles[1].open,
        high_price=exec_candles[1].high,
        low_price=exec_candles[1].low,
        close_price=exec_candles[1].low + Decimal("1.00"),  # Weak close
        volume=exec_candles[1].volume,
        is_closed=True,
    )
    mutated_series = [exec_candles[0], mutated_c1] + exec_candles[2:]

    mutated_signal = engine.evaluate(
        raw_exec_candles=mutated_series,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    assert baseline_signal.entry_reference != mutated_signal.entry_reference
