"""
AlphaForge Mathematical Strategy Rule Evaluators.
Contains isolated, pure evaluation functions implementing the rules defined
in STRATEGY_RULE_CATALOG.md.
"""

from collections.abc import Sequence
from decimal import Decimal

from alphaforge.core.enums import SignalDirection, TrendState
from alphaforge.core.models import Candle
from alphaforge.strategy.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_candle_geometry,
    calculate_ema,
    calculate_rsi,
)


def evaluate_trend_regime(
    conf_candles: list[Candle],
    fast_period: int = 9,
    slow_period: int = 21,
    enable_regime_filter: bool = False,
    min_adx_threshold: Decimal = Decimal("20.0"),
    min_ema_spread_pct: Decimal = Decimal("0.0008"),
) -> tuple[TrendState, Decimal, Decimal]:
    """
    Evaluate higher-timeframe trend regime on closed candles.
    Returns (TrendState, ema_fast, ema_slow).
    If enable_regime_filter is True:
      - Validates minimum EMA spread to reject tangled/flat EMAs in sideways markets.
      - Validates ADX(14) >= min_adx_threshold to reject non-trending/choppy regimes.
    """
    if len(conf_candles) < slow_period:
        return TrendState.NEUTRAL, Decimal("0"), Decimal("0")

    closes = [c.close for c in conf_candles]
    ema_fast_series = calculate_ema(closes, fast_period)
    ema_slow_series = calculate_ema(closes, slow_period)

    latest_close = closes[-1]
    latest_fast = ema_fast_series[-1]
    latest_slow = ema_slow_series[-1]

    if enable_regime_filter:
        # 1. EMA Spread Gate: Reject tangled EMAs in ranging markets
        if latest_close > Decimal("0"):
            spread_pct = abs(latest_fast - latest_slow) / latest_close
            if spread_pct < min_ema_spread_pct:
                return TrendState.NEUTRAL, latest_fast, latest_slow

        # 2. ADX Directional Strength Gate: Fail-closed on insufficient history, reject weak/choppy markets
        if len(conf_candles) < 28:
            return TrendState.NEUTRAL, latest_fast, latest_slow

        highs = [c.high for c in conf_candles]
        lows = [c.low for c in conf_candles]
        adx_series = calculate_adx(highs, lows, closes, period=14)
        latest_adx = adx_series[-1]
        if latest_adx < min_adx_threshold:
            return TrendState.NEUTRAL, latest_fast, latest_slow

    if latest_fast > latest_slow and latest_close > latest_slow:
        return TrendState.BULLISH, latest_fast, latest_slow
    elif latest_fast < latest_slow and latest_close < latest_slow:
        return TrendState.BEARISH, latest_fast, latest_slow
    else:
        return TrendState.NEUTRAL, latest_fast, latest_slow


def evaluate_breakout(
    closed_exec_candles: list[Candle], lookback: int = 20
) -> tuple[bool, bool, Decimal, Decimal]:
    """
    Evaluate whether the latest closed candle [1] breaks out of the prior 20-period swing range.
    closed_exec_candles[-1] is trigger candle [1].
    closed_exec_candles[-lookback-1 : -1] are the prior reference candles.
    Returns (is_long_breakout, is_short_breakout, resistance_level, support_level).
    """
    if len(closed_exec_candles) < lookback + 1:
        return False, False, Decimal("0"), Decimal("0")

    trigger_candle = closed_exec_candles[-1]
    prior_candles = closed_exec_candles[-(lookback + 1) : -1]

    highs = [c.high for c in prior_candles]
    lows = [c.low for c in prior_candles]

    resistance = max(highs)
    support = min(lows)

    is_long = trigger_candle.close > resistance
    is_short = trigger_candle.close < support

    return is_long, is_short, resistance, support


def evaluate_candle_geometry(
    trigger_candle: Candle,
    direction: SignalDirection,
    min_body_ratio: Decimal,
    min_close_location_ratio: Decimal,
) -> tuple[bool, Decimal, Decimal]:
    """
    Evaluate candle geometry: body percentage and close location within candle range.
    Returns (is_valid, body_ratio, close_location_ratio).
    """
    body_ratio, close_loc_long, close_loc_short = calculate_candle_geometry(
        trigger_candle.open, trigger_candle.high, trigger_candle.low, trigger_candle.close
    )

    if body_ratio < min_body_ratio:
        return False, body_ratio, Decimal("0")

    if direction == SignalDirection.LONG:
        is_loc_valid = close_loc_long >= min_close_location_ratio
        return is_loc_valid, body_ratio, close_loc_long
    elif direction == SignalDirection.SHORT:
        is_loc_valid = close_loc_short >= min_close_location_ratio
        return is_loc_valid, body_ratio, close_loc_short

    return False, body_ratio, Decimal("0")


def evaluate_volume_spike(
    closed_exec_candles: list[Candle],
    lookback: int = 20,
    min_relative_volume: Decimal = Decimal("1.20"),
) -> tuple[bool, Decimal]:
    """
    Evaluate whether the trigger candle volume is >= min_relative_volume * 20-period moving average.
    Returns (is_confirmed, volume_ratio).
    """
    if len(closed_exec_candles) < lookback + 1:
        return False, Decimal("0")

    trigger_vol = Decimal(closed_exec_candles[-1].volume)
    prior_vols = [Decimal(c.volume) for c in closed_exec_candles[-(lookback + 1) : -1]]
    sma_vol = sum(prior_vols) / Decimal(lookback)

    if sma_vol <= Decimal("0"):
        return False, Decimal("0")

    vol_ratio = trigger_vol / sma_vol
    is_confirmed = vol_ratio >= min_relative_volume

    return is_confirmed, vol_ratio


def evaluate_momentum(
    closed_exec_candles: list[Candle],
    direction: SignalDirection,
    period: int = 14,
    rsi_min: Decimal = Decimal("50.0"),
    rsi_max: Decimal = Decimal("75.0"),
) -> tuple[bool, Decimal]:
    """
    Evaluate whether RSI is within confirmed momentum boundaries.
    Returns (is_confirmed, current_rsi).
    """
    if len(closed_exec_candles) <= period:
        return False, Decimal("50.0")

    closes = [c.close for c in closed_exec_candles]
    rsi_series = calculate_rsi(closes, period)
    current_rsi = rsi_series[-1]

    if direction == SignalDirection.LONG:
        is_valid = rsi_min < current_rsi <= rsi_max
        return is_valid, current_rsi
    elif direction == SignalDirection.SHORT:
        is_valid = rsi_min <= current_rsi < rsi_max
        return is_valid, current_rsi

    return False, current_rsi


def evaluate_volatility(
    closed_exec_candles: list[Candle],
    period: int = 14,
    min_pct: Decimal = Decimal("0.0005"),
    max_pct: Decimal = Decimal("0.0150"),
) -> tuple[bool, Decimal]:
    """
    Evaluate whether ATR is within acceptable minimum and maximum price percentage bounds.
    Returns (is_valid, current_atr).
    """
    if len(closed_exec_candles) < period:
        return False, Decimal("0")

    highs = [c.high for c in closed_exec_candles]
    lows = [c.low for c in closed_exec_candles]
    closes = [c.close for c in closed_exec_candles]

    atr_series = calculate_atr(highs, lows, closes, period)
    current_atr = atr_series[-1]
    latest_close = closes[-1]

    min_atr = latest_close * min_pct
    max_atr = latest_close * max_pct

    is_valid = min_atr <= current_atr <= max_atr
    return is_valid, current_atr


def calculate_stops_and_targets(
    trigger_candle: Candle,
    swing_candles: Candle | Sequence[Candle],
    current_atr: Decimal,
    direction: SignalDirection,
    atr_multiplier: Decimal = Decimal("1.0"),
    target_multiplier: Decimal = Decimal("2.0"),
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Calculate deterministic entry, stop-loss, target, and risk distance.
    Evaluates structural swing over the provided swing candles.
    Returns (entry, stop, target, risk_distance).
    """
    entry = trigger_candle.close
    atr_buffer = current_atr * atr_multiplier

    if isinstance(swing_candles, Candle):
        candles_to_check = [trigger_candle, swing_candles]
    else:
        candles_to_check = list(swing_candles)
        if trigger_candle not in candles_to_check:
            candles_to_check.append(trigger_candle)

    if not candles_to_check:
        return entry, Decimal("0"), Decimal("0"), Decimal("0")

    if direction == SignalDirection.LONG:
        structural_low = min(c.low for c in candles_to_check)
        stop = structural_low - atr_buffer
        risk_distance = entry - stop
        target = entry + (risk_distance * target_multiplier)
    elif direction == SignalDirection.SHORT:
        structural_high = max(c.high for c in candles_to_check)
        stop = structural_high + atr_buffer
        risk_distance = stop - entry
        target = entry - (risk_distance * target_multiplier)
    else:
        return entry, Decimal("0"), Decimal("0"), Decimal("0")

    return entry, stop, target, risk_distance
