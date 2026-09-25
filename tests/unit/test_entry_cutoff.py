"""
Unit tests for Afternoon Entry Cut-off Time Guard (Upgrade 3).
Verifies that no new entry signals are generated after 14:30 IST,
preventing late-session entries that do not have enough runway to reach +2R.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import generate_candle_series


def test_entry_cutoff_before_1430_allowed() -> None:
    # 10:30 IST = 05:00 UTC (well before 14:30 IST)
    base_time = datetime(2026, 9, 24, 5, 0, 0, tzinfo=UTC)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(
        base_time, count=30, interval_minutes=15, trend_type="bullish"
    )
    eval_time = exec_candles[1].timestamp

    cfg = StrategyConfig(enable_entry_cutoff=True, entry_cutoff_time_ist="14:30")
    engine = DeterministicStrategyEngine(config=cfg)

    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    # Must NOT be rejected with REJECT_ENTRY_CUTOFF
    assert signal.rejection_code != RejectionCode.REJECT_ENTRY_CUTOFF


def test_entry_cutoff_after_1430_rejected() -> None:
    # 14:36 IST = 09:06 UTC (after 14:30 IST cutoff)
    base_time = datetime(2026, 9, 24, 9, 6, 0, tzinfo=UTC)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(
        base_time, count=30, interval_minutes=15, trend_type="bullish"
    )
    eval_time = exec_candles[1].timestamp

    cfg = StrategyConfig(enable_entry_cutoff=True, entry_cutoff_time_ist="14:30")
    engine = DeterministicStrategyEngine(config=cfg)

    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    # Must be REJECTED with REJECT_ENTRY_CUTOFF
    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_ENTRY_CUTOFF
    assert signal.direction == SignalDirection.FLAT


def test_entry_cutoff_disabled_allows_late_trades() -> None:
    # 14:36 IST = 09:06 UTC with enable_entry_cutoff=False
    base_time = datetime(2026, 9, 24, 9, 6, 0, tzinfo=UTC)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(
        base_time, count=30, interval_minutes=15, trend_type="bullish"
    )
    eval_time = exec_candles[1].timestamp

    cfg = StrategyConfig(enable_entry_cutoff=False, entry_cutoff_time_ist="14:30")
    engine = DeterministicStrategyEngine(config=cfg)

    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_time,
    )

    # When disabled, must NOT be rejected by cutoff
    assert signal.rejection_code != RejectionCode.REJECT_ENTRY_CUTOFF
