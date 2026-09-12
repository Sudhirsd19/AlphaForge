"""
Golden fixture test suite for AlphaForge Strategy Specification.
Covers all 14 mandatory scenario cases defined in Section 22:
1. valid long
2. valid short
3. trend rejection
4. breakout rejection
5. volume rejection
6. momentum rejection
7. volatility rejection
8. futures rejection
9. stale data
10. invalid data
11. expired setup
12. invalid stop
13. invalid target
14. duplicate signal
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.core.models import Candle
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from tests.helpers import create_candle, generate_candle_series


def build_scenario_candles(
    base_time: datetime,
    trend_type: str = "bullish",
    breakout_valid: bool = True,
    volume_spike: bool = True,
    good_geometry: bool = True,
) -> tuple[list[Candle], list[Candle]]:
    """Construct precisely controlled candles for scenario tests."""
    exec_interval = 3
    conf_interval = 15
    base_price = Decimal("24000.00")

    bullish_deltas = [
        Decimal(x) for x in [4, -3, 5, -4, 4, -2, 5, -3, 4, -3, 5, -4, 4, -2, 5, -3, 4, -3, 5, -2]
    ]
    bearish_deltas = [-d for d in bullish_deltas]

    chrono_exec: list[Candle] = []
    t = base_time - timedelta(minutes=exec_interval * 22)
    p = base_price

    # Generate 20 baseline reference bars
    for i in range(20):
        t += timedelta(minutes=exec_interval)
        if trend_type == "bullish":
            p += bullish_deltas[i]
            chrono_exec.append(
                create_candle(
                    timestamp=t,
                    open_price=p - Decimal("6.00"),
                    high_price=p + Decimal("12.00"),
                    low_price=p - Decimal("8.00"),
                    close_price=p,
                    volume=1000,
                    is_closed=True,
                )
            )
        else:  # Bearish or flat
            p += bearish_deltas[i]
            chrono_exec.append(
                create_candle(
                    timestamp=t,
                    open_price=p + Decimal("6.00"),
                    high_price=p + Decimal("8.00"),
                    low_price=p - Decimal("12.00"),
                    close_price=p,
                    volume=1000,
                    is_closed=True,
                )
            )

    # Reference resistance & support
    prior_resistance = max(c.high for c in chrono_exec)
    prior_support = min(c.low for c in chrono_exec)

    # Trigger candle [1]
    t += timedelta(minutes=exec_interval)
    if trend_type == "bullish":
        if breakout_valid:
            c1_close = prior_resistance + Decimal("15.00")
            c1_open = prior_resistance + (Decimal("2.00") if good_geometry else Decimal("14.00"))
            c1_low = prior_resistance - Decimal("1.00")
            c1_high = c1_close + (Decimal("2.00") if good_geometry else Decimal("20.00"))
        else:
            c1_close = prior_resistance - Decimal("5.00")  # Inside range
            c1_open = prior_resistance - Decimal("10.00")
            c1_low = prior_resistance - Decimal("15.00")
            c1_high = prior_resistance - Decimal("2.00")
    else:  # Bearish
        if breakout_valid:
            c1_close = prior_support - Decimal("15.00")
            c1_open = prior_support - (Decimal("2.00") if good_geometry else Decimal("14.00"))
            c1_high = prior_support + Decimal("1.00")
            c1_low = c1_close - (Decimal("2.00") if good_geometry else Decimal("20.00"))
        else:
            c1_close = prior_support + Decimal("5.00")
            c1_open = prior_support + Decimal("10.00")
            c1_low = prior_support + Decimal("2.00")
            c1_high = prior_support + Decimal("15.00")

    c1_volume = 1500 if volume_spike else 500  # 1500 > 1.20 * 1000

    trigger_candle = create_candle(
        timestamp=t,
        open_price=c1_open,
        high_price=c1_high,
        low_price=c1_low,
        close_price=c1_close,
        volume=c1_volume,
        is_closed=True,
    )
    chrono_exec.append(trigger_candle)

    # Forming candle [0]
    t += timedelta(minutes=exec_interval)
    forming_candle = create_candle(
        timestamp=t,
        open_price=c1_close,
        high_price=c1_close + Decimal("5.00"),
        low_price=c1_close - Decimal("5.00"),
        close_price=c1_close + Decimal("1.00"),
        volume=200,
        is_closed=False,
    )

    # Raw exec list: [0] forming, [1] trigger, [2] prior...
    raw_exec = [forming_candle] + list(reversed(chrono_exec))

    # Confirmation timeframe candles (15m)
    conf_candles: list[Candle] = []
    t_conf = base_time - timedelta(minutes=conf_interval * 30)
    p_conf = base_price
    for i in range(29):
        t_conf += timedelta(minutes=conf_interval)
        if trend_type == "bullish":
            p_conf += Decimal("20.00")
            c_open = p_conf - Decimal("5.00")
            c_high = p_conf + Decimal("10.00")
            c_low = p_conf - Decimal("10.00")
            c_close = p_conf + Decimal("5.00")
        elif trend_type == "bearish":
            p_conf -= Decimal("20.00")
            c_open = p_conf + Decimal("5.00")
            c_high = p_conf + Decimal("10.00")
            c_low = p_conf - Decimal("10.00")
            c_close = p_conf - Decimal("5.00")
        else:  # flat / neutral
            if i < 28:
                p_conf = base_price + (Decimal("4.00") if i % 2 == 0 else Decimal("-4.00"))
                c_open = p_conf - Decimal("2.00")
                c_high = p_conf + Decimal("5.00")
                c_low = p_conf - Decimal("5.00")
                c_close = p_conf
            else:
                p_conf = base_price
                c_open = p_conf
                c_high = p_conf + Decimal("5.00")
                c_low = p_conf - Decimal("2.00")
                c_close = p_conf

        conf_candles.append(
            create_candle(
                timestamp=t_conf,
                open_price=c_open,
                high_price=c_high,
                low_price=c_low,
                close_price=c_close,
                volume=5000,
                is_closed=True,
            )
        )
    return raw_exec, conf_candles


def test_golden_scenario_01_valid_long() -> None:
    """Fixture 1: Valid Long setup meeting all criteria produces ACCEPT."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.ACCEPT
    assert signal.rejection_code == RejectionCode.REJECT_NONE
    assert signal.direction == SignalDirection.LONG
    assert signal.entry_reference > Decimal("0")
    assert signal.stop_reference < signal.entry_reference
    assert signal.target_reference > signal.entry_reference
    assert signal.risk_distance == signal.entry_reference - signal.stop_reference


def test_golden_scenario_02_valid_short() -> None:
    """Fixture 2: Valid Short setup meeting all criteria produces ACCEPT."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bearish")
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.ACCEPT
    assert signal.rejection_code == RejectionCode.REJECT_NONE
    assert signal.direction == SignalDirection.SHORT
    assert signal.entry_reference > Decimal("0")
    assert signal.stop_reference > signal.entry_reference
    assert signal.target_reference < signal.entry_reference
    assert signal.risk_distance == signal.stop_reference - signal.entry_reference


def test_golden_scenario_03_trend_rejection() -> None:
    """Fixture 3: Neutral higher timeframe trend returns REJECT_TREND."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="flat")
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_TREND


def test_golden_scenario_04_breakout_rejection() -> None:
    """Fixture 4: Trigger candle inside range returns REJECT_BREAKOUT."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish", breakout_valid=False)
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_BREAKOUT


def test_golden_scenario_05_volume_rejection() -> None:
    """Fixture 5: Volume below 1.20x moving average returns REJECT_VOLUME."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish", volume_spike=False)
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_VOLUME


def test_golden_scenario_06_momentum_rejection() -> None:
    """Fixture 6: RSI out of confirmed bounds returns REJECT_MOMENTUM."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    # Set impossible RSI bounds in config
    cfg = StrategyConfig(rsi_long_min=Decimal("80.0"), rsi_long_max=Decimal("95.0"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_MOMENTUM


def test_golden_scenario_07_volatility_rejection() -> None:
    """Fixture 7: ATR below minimum bounds returns REJECT_VOLATILITY."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    # Set impossible ATR minimum
    cfg = StrategyConfig(atr_min_pct=Decimal("0.1000"))  # 10% of price
    engine = DeterministicStrategyEngine(cfg)
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_VOLATILITY


def test_golden_scenario_08_futures_rejection() -> None:
    """Fixture 8: Futures confirmation status NOT_CONFIRMED returns REJECT_FUTURES_CONFIRMATION."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.NOT_CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_FUTURES_CONFIRMATION


def test_golden_scenario_09_stale_data() -> None:
    """Fixture 9: Stale data returns REJECT_DATA_STALE."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    engine = DeterministicStrategyEngine()
    stale_eval_time = exec_c[1].timestamp + timedelta(minutes=30)

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, stale_eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_DATA_STALE


def test_golden_scenario_10_invalid_data() -> None:
    """Fixture 10: Invalid OHLC invariant raises DataIntegrityError."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(DataIntegrityError):
        # High lower than Open is invalid
        create_candle(base_time, "24000.00", "23900.00", "23800.00", "23950.00")


def test_golden_scenario_11_expired_setup() -> None:
    """Fixture 11: Setup evaluated past maximum window is flagged."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    engine = DeterministicStrategyEngine()
    # Evaluate 10 minutes late
    late_time = exec_c[1].timestamp + timedelta(minutes=10)

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, late_time)
    assert signal.decision in (StrategyDecision.REJECT, StrategyDecision.EXPIRED)


def test_golden_scenario_12_invalid_stop() -> None:
    """Fixture 12: Risk distance below minimum threshold returns REJECT_INVALID_STOP."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    # Set min_risk_distance_pct to 50%
    cfg = StrategyConfig(min_risk_distance_pct=Decimal("0.50"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_INVALID_STOP


def test_golden_scenario_13_invalid_target() -> None:
    """Fixture 13: Target multiple <= 0 produces REJECT_INVALID_TARGET."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    cfg = StrategyConfig(target_risk_multiple=Decimal("-1.0"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = exec_c[1].timestamp

    signal = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_INVALID_TARGET


def test_golden_scenario_14_duplicate_signal() -> None:
    """Fixture 14: Re-evaluating the identical bar setup emits DUPLICATE."""
    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    exec_c, conf_c = build_scenario_candles(base_time, trend_type="bullish")
    engine = DeterministicStrategyEngine()
    eval_time = exec_c[1].timestamp

    sig1 = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)
    assert sig1.decision == StrategyDecision.ACCEPT

    sig2 = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)
    assert sig2.decision == StrategyDecision.DUPLICATE
    assert sig2.rejection_code == RejectionCode.REJECT_DUPLICATE
