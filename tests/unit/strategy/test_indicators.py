"""
Unit tests for pure vectorized indicators in alphaforge/strategy/indicators.py.
"""

from decimal import Decimal

from alphaforge.strategy.indicators import (
    calculate_atr,
    calculate_candle_geometry,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
    calculate_swing_levels,
    calculate_true_range,
)


def test_calculate_sma() -> None:
    values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")]
    sma = calculate_sma(values, period=3)
    assert sma[0] == Decimal("0")
    assert sma[1] == Decimal("0")
    assert sma[2] == Decimal("20.0000")  # (10+20+30)/3
    assert sma[3] == Decimal("30.0000")  # (20+30+40)/3
    assert sma[4] == Decimal("40.0000")  # (30+40+50)/3


def test_calculate_ema() -> None:
    values = [Decimal(str(x)) for x in [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]]
    ema = calculate_ema(values, period=5)
    # Seed SMA of first 5 values: (10+11+12+13+14)/5 = 12.0000
    assert ema[4] == Decimal("12.0000")
    # Multiplier = 2 / 6 = 1/3
    # Next value = 15: (15 - 12) * (1/3) + 12 = 13.0000
    assert ema[5] == Decimal("13.0000")


def test_calculate_true_range() -> None:
    highs = [Decimal("100"), Decimal("105"), Decimal("95")]
    lows = [Decimal("90"), Decimal("98"), Decimal("90")]
    closes = [Decimal("95"), Decimal("102"), Decimal("92")]

    tr = calculate_true_range(highs, lows, closes)
    assert tr[0] == Decimal("10")  # 100 - 90
    assert tr[1] == Decimal("10")  # max(105-98=7, |105-95|=10, |98-95|=3)
    assert tr[2] == Decimal("12")  # max(95-90=5, |95-102|=7, |90-102|=12)


def test_calculate_atr() -> None:
    highs = [Decimal("100") + Decimal(i) for i in range(20)]
    lows = [Decimal("90") + Decimal(i) for i in range(20)]
    closes = [Decimal("95") + Decimal(i) for i in range(20)]

    atr = calculate_atr(highs, lows, closes, period=14)
    # TR is constant 10 for each bar
    assert atr[-1] == Decimal("10.0000")


def test_calculate_rsi() -> None:
    # 15 monotonically increasing prices gives RSI = 100
    increasing = [Decimal("100") + Decimal(i * 2) for i in range(20)]
    rsi_inc = calculate_rsi(increasing, period=14)
    assert rsi_inc[-1] == Decimal("100.0000")

    # Monotonically decreasing prices gives RSI = 0
    decreasing = [Decimal("200") - Decimal(i * 2) for i in range(20)]
    rsi_dec = calculate_rsi(decreasing, period=14)
    assert rsi_dec[-1] == Decimal("0.0000")


def test_calculate_swing_levels() -> None:
    highs = [Decimal(i) for i in range(30)]
    lows = [Decimal(i - 10) for i in range(30)]

    resistance, support = calculate_swing_levels(highs, lows, lookback=20)
    assert resistance == Decimal("29")
    assert support == Decimal("0")  # 10 - 10 = 0


def test_calculate_candle_geometry() -> None:
    # Strong bullish candle: open=100, low=99, close=109, high=110
    # Range = 11, Body = 9, BodyRatio = 9/11 = 0.8182
    # CloseLocLong = (109 - 99)/11 = 10/11 = 0.9091
    body_ratio, close_loc_long, close_loc_short = calculate_candle_geometry(
        Decimal("100"), Decimal("110"), Decimal("99"), Decimal("109")
    )
    assert body_ratio > Decimal("0.80")
    assert close_loc_long > Decimal("0.90")
    assert close_loc_short < Decimal("0.20")


def test_calculate_adx() -> None:
    from alphaforge.strategy.indicators import calculate_adx

    # Trending series: price consistently expanding upward
    highs_trend = [Decimal(str(100 + i * 2)) for i in range(40)]
    lows_trend = [Decimal(str(95 + i * 2)) for i in range(40)]
    closes_trend = [Decimal(str(98 + i * 2)) for i in range(40)]

    adx_trend = calculate_adx(highs_trend, lows_trend, closes_trend, period=14)
    assert len(adx_trend) == 40
    # In a strong trending market, ADX must be high (> 50)
    assert adx_trend[-1] > Decimal("50.0")

    # Choppy / ranging series: price oscillates within narrow boundary
    highs_chop = [Decimal(str(100 + (i % 2) * 2)) for i in range(40)]
    lows_chop = [Decimal(str(95 - (i % 2) * 2)) for i in range(40)]
    closes_chop = [Decimal(str(98 + (i % 2))) for i in range(40)]

    adx_chop = calculate_adx(highs_chop, lows_chop, closes_chop, period=14)
    # In a choppy market, ADX must be low (< 20)
    assert adx_chop[-1] < Decimal("20.0")
