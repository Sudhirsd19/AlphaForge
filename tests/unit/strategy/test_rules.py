"""
Unit tests for isolated strategy rule evaluators in alphaforge/strategy/rules.py.
"""

from decimal import Decimal

from alphaforge.core.enums import SignalDirection, TrendState
from alphaforge.strategy.rules import (
    calculate_stops_and_targets,
    evaluate_breakout,
    evaluate_candle_geometry,
    evaluate_momentum,
    evaluate_trend_regime,
    evaluate_volatility,
    evaluate_volume_spike,
)
from tests.helpers import create_candle
from datetime import datetime, timezone


def test_evaluate_trend_regime_insufficient_bars() -> None:
    candles = [create_candle(datetime(2026, 9, 12, 10, i, tzinfo=timezone.utc), "100", "110", "90", "105") for i in range(10)]
    trend, fast, slow = evaluate_trend_regime(candles, 9, 21)
    assert trend == TrendState.NEUTRAL


def test_evaluate_breakout_inside_range() -> None:
    candles = [
        create_candle(datetime(2026, 9, 12, 10, i, tzinfo=timezone.utc), "100", "110", "90", "100")
        for i in range(25)
    ]
    is_long, is_short, res, sup = evaluate_breakout(candles, lookback=20)
    assert not is_long
    assert not is_short
    assert res == Decimal("110")
    assert sup == Decimal("90")


def test_evaluate_candle_geometry_doji_rejected() -> None:
    # Doji: open=100, high=110, low=90, close=100.5 (body = 0.5, range = 20, ratio = 0.025)
    doji = create_candle(datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc), "100.00", "110.00", "90.00", "100.50")
    is_valid, body_ratio, _ = evaluate_candle_geometry(doji, SignalDirection.LONG, Decimal("0.50"), Decimal("0.70"))
    assert not is_valid
    assert body_ratio < Decimal("0.10")


def test_calculate_stops_and_targets_math() -> None:
    t = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
    c1 = create_candle(t, "24000", "24050", "23950", "24040")
    c2 = create_candle(t, "23950", "24010", "23920", "24000")
    atr = Decimal("30.00")

    # Long: structural low = min(23950, 23920) = 23920
    # stop = 23920 - 30 = 23890
    # risk_distance = 24040 - 23890 = 150
    # target = 24040 + 2 * 150 = 24340
    entry, stop, target, risk_dist = calculate_stops_and_targets(
        c1, c2, atr, SignalDirection.LONG, Decimal("1.0"), Decimal("2.0")
    )
    assert entry == Decimal("24040")
    assert stop == Decimal("23890")
    assert risk_dist == Decimal("150")
    assert target == Decimal("24340")
