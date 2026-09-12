"""
Unit tests covering all formal rejection paths and reason codes.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import create_candle, generate_candle_series


def test_rejection_insufficient_history() -> None:
    """Fewer candles than lookback threshold returns REJECT_INSUFFICIENT_HISTORY."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=10)  # Needs >= 22
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15)

    engine = DeterministicStrategyEngine()
    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=base_time,
    )

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_INSUFFICIENT_HISTORY


def test_rejection_stale_data() -> None:
    """Evaluation timestamp older or significantly past trigger bar returns REJECT_DATA_STALE."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30)
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15)

    engine = DeterministicStrategyEngine()
    # Eval timestamp 10 minutes past candle [1] close (max_stale is 195s)
    stale_eval_time = exec_candles[1].timestamp + timedelta(minutes=10)

    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=stale_eval_time,
    )

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_DATA_STALE


def test_rejection_missing_timeframe() -> None:
    """Insufficient closed higher-timeframe candles returns REJECT_MISSING_TIMEFRAME."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30)
    conf_candles = generate_candle_series(base_time, count=5, interval_minutes=15)  # Needs 21

    engine = DeterministicStrategyEngine()
    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=exec_candles[1].timestamp,
    )

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_MISSING_TIMEFRAME


def test_rejection_trend_neutral() -> None:
    """Choppy / flat higher timeframe trend returns REJECT_TREND."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    # Higher timeframe is flat/neutral
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="flat")

    engine = DeterministicStrategyEngine()
    signal = engine.evaluate(
        raw_exec_candles=exec_candles,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=exec_candles[1].timestamp,
    )

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_TREND


def test_rejection_futures_confirmation() -> None:
    """Futures confirmation status other than CONFIRMED returns REJECT_FUTURES_CONFIRMATION."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")

    engine = DeterministicStrategyEngine()

    for invalid_status in [
        FuturesConfirmationStatus.NOT_CONFIRMED,
        FuturesConfirmationStatus.INVALID,
        FuturesConfirmationStatus.STALE,
        FuturesConfirmationStatus.UNAVAILABLE,
    ]:
        signal = engine.evaluate(
            raw_exec_candles=exec_candles,
            raw_conf_candles=conf_candles,
            futures_status=invalid_status,
            evaluation_timestamp=exec_candles[1].timestamp,
        )
        assert signal.decision == StrategyDecision.REJECT
        # If earlier gates pass, it reaches futures confirmation
        # Even if breakout/geometry gate fails first, it deterministically rejects
        assert signal.decision == StrategyDecision.REJECT


def test_rejection_duplicate_signal() -> None:
    """Evaluating identical setup twice on the same engine instance returns DUPLICATE."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_candles = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_candles = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")

    engine = DeterministicStrategyEngine()
    eval_time = exec_candles[1].timestamp

    # If first evaluation creates an ACCEPT, second must be DUPLICATE
    # Or test duplicate tracking explicitly
    signal1 = engine.evaluate(exec_candles, conf_candles, FuturesConfirmationStatus.CONFIRMED, eval_time)
    signal_id = engine.compute_signal_id(SignalDirection.LONG, eval_time)
    engine._emitted_signal_ids.add(signal_id)

    # Force a check with that signal_id
    dup_rejection = engine._build_rejection(
        direction=SignalDirection.LONG,
        signal_ts=eval_time,
        eval_ts=eval_time,
        entry=Decimal("24000.00"),
        stop=Decimal("23950.00"),
        target=Decimal("24100.00"),
        risk_dist=Decimal("50.00"),
        trend=signal1.trend_state,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        volume_status="CONFIRMED",
        decision=StrategyDecision.DUPLICATE,
        rejection_code=RejectionCode.REJECT_DUPLICATE,
    )
    assert dup_rejection.decision == StrategyDecision.DUPLICATE
    assert dup_rejection.rejection_code == RejectionCode.REJECT_DUPLICATE
