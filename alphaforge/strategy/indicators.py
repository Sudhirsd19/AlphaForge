"""
AlphaForge Pure Vectorized Indicator Engine.
All indicators are pure, side-effect-free functions operating on immutable lists of Decimal values.
Guarantees identical, deterministic mathematical output across all operating systems.
"""

from decimal import ROUND_HALF_EVEN, Decimal

PRECISION = Decimal("0.0001")


def calculate_sma(values: list[Decimal], period: int) -> list[Decimal]:
    """
    Calculate Simple Moving Average over a sequence of Decimal values.
    Returns list of same length as values; values before index (period - 1)
    are filled with Decimal(0).
    """
    if len(values) < period:
        return [Decimal("0")] * len(values)

    result: list[Decimal] = [Decimal("0")] * (period - 1)
    current_sum = sum(values[:period])
    result.append((current_sum / Decimal(period)).quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    for i in range(period, len(values)):
        current_sum += values[i] - values[i - period]
        result.append((current_sum / Decimal(period)).quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    return result


def calculate_ema(values: list[Decimal], period: int) -> list[Decimal]:
    """
    Calculate Exponential Moving Average over a sequence of Decimal values.
    Initial value is seeded with SMA of the first `period` elements.
    Multiplier k = 2 / (period + 1).
    """
    if len(values) < period:
        return [Decimal("0")] * len(values)

    result: list[Decimal] = [Decimal("0")] * (period - 1)
    # Seed with SMA
    sma_seed = sum(values[:period]) / Decimal(period)
    current_ema = sma_seed.quantize(PRECISION, rounding=ROUND_HALF_EVEN)
    result.append(current_ema)

    multiplier = Decimal("2") / Decimal(period + 1)

    for i in range(period, len(values)):
        current_ema = (values[i] - current_ema) * multiplier + current_ema
        result.append(current_ema.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    return result


def calculate_true_range(
    highs: list[Decimal], lows: list[Decimal], closes: list[Decimal]
) -> list[Decimal]:
    """
    Calculate True Range sequence for OHLC price series.
    TR_t = max(H_t - L_t, abs(H_t - C_{t-1}), abs(L_t - C_{t-1})).
    For t=0, TR_0 = H_0 - L_0.
    """
    if not highs:
        return []

    tr_list: list[Decimal] = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(h_l, h_pc, l_pc))

    return tr_list


def calculate_atr(
    highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14
) -> list[Decimal]:
    """
    Calculate Average True Range using Wilder's smoothing method.
    """
    if len(highs) < period:
        return [Decimal("0")] * len(highs)

    tr_list = calculate_true_range(highs, lows, closes)
    result: list[Decimal] = [Decimal("0")] * (period - 1)

    # Initial ATR is simple average of first `period` true ranges
    current_atr = sum(tr_list[:period]) / Decimal(period)
    result.append(current_atr.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    dec_period = Decimal(period)
    dec_period_minus_1 = dec_period - Decimal("1")

    for i in range(period, len(tr_list)):
        current_atr = (current_atr * dec_period_minus_1 + tr_list[i]) / dec_period
        result.append(current_atr.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    return result


def calculate_rsi(closes: list[Decimal], period: int = 14) -> list[Decimal]:
    """
    Calculate Relative Strength Index (RSI) using Wilder's smoothing method.
    """
    if len(closes) <= period:
        return [Decimal("50.0")] * len(closes)

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [c if c > Decimal("0") else Decimal("0") for c in changes]
    losses = [abs(c) if c < Decimal("0") else Decimal("0") for c in changes]

    result: list[Decimal] = [Decimal("50.0")] * period  # 1 (diff) + (period - 1)

    # Initial averages
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    if avg_loss == Decimal("0"):
        first_rsi = Decimal("100.0") if avg_gain > Decimal("0") else Decimal("50.0")
    else:
        rs = avg_gain / avg_loss
        first_rsi = Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))

    result.append(first_rsi.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    dec_period = Decimal(period)
    dec_period_minus_1 = dec_period - Decimal("1")

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * dec_period_minus_1 + gains[i]) / dec_period
        avg_loss = (avg_loss * dec_period_minus_1 + losses[i]) / dec_period

        if avg_loss == Decimal("0"):
            rsi_val = Decimal("100.0") if avg_gain > Decimal("0") else Decimal("50.0")
        else:
            rs = avg_gain / avg_loss
            rsi_val = Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))

        result.append(rsi_val.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    return result


def calculate_swing_levels(
    highs: list[Decimal], lows: list[Decimal], lookback: int = 20
) -> tuple[Decimal, Decimal]:
    """
    Compute resistance (max high) and support (min low) over trailing lookback window.
    """
    window_highs = highs[-lookback:]
    window_lows = lows[-lookback:]
    return max(window_highs), min(window_lows)


def calculate_candle_geometry(
    open_p: Decimal, high_p: Decimal, low_p: Decimal, close_p: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate candle body ratio and directional close location ratios.
    Returns (body_ratio, close_location_long, close_location_short).
    """
    candle_range = high_p - low_p
    if candle_range <= Decimal("0"):
        return Decimal("0"), Decimal("0"), Decimal("0")

    body = abs(close_p - open_p)
    body_ratio = (body / candle_range).quantize(PRECISION, rounding=ROUND_HALF_EVEN)
    close_loc_long = ((close_p - low_p) / candle_range).quantize(
        PRECISION, rounding=ROUND_HALF_EVEN
    )
    close_loc_short = ((high_p - close_p) / candle_range).quantize(
        PRECISION, rounding=ROUND_HALF_EVEN
    )

    return body_ratio, close_loc_long, close_loc_short


def calculate_adx(
    highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14
) -> list[Decimal]:
    """
    Calculate Average Directional Index (ADX) using Wilder's directional movement smoothing.
    Returns a list of Decimal values representing ADX at each bar.
    Bars prior to (2 * period - 1) are seeded with Decimal("0.0").
    """
    n = len(highs)
    if n < 2 * period or len(lows) < n or len(closes) < n:
        return [Decimal("0.0")] * n

    # Step 1: Directional Movement (+DM, -DM) and True Range (TR)
    plus_dm: list[Decimal] = [Decimal("0.0")]
    minus_dm: list[Decimal] = [Decimal("0.0")]
    tr_list: list[Decimal] = [highs[0] - lows[0]]

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > Decimal("0") and up_move > down_move:
            plus_dm.append(up_move)
        else:
            plus_dm.append(Decimal("0.0"))

        if down_move > Decimal("0") and down_move > up_move:
            minus_dm.append(down_move)
        else:
            minus_dm.append(Decimal("0.0"))

        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(h_l, h_pc, l_pc))

    # Step 2: Wilder's smoothing for TR, +DM, -DM
    smooth_tr = sum(tr_list[1 : period + 1])
    smooth_plus_dm = sum(plus_dm[1 : period + 1])
    smooth_minus_dm = sum(minus_dm[1 : period + 1])

    dec_period = Decimal(period)
    dec_period_minus_1 = dec_period - Decimal("1")

    dx_list: list[Decimal] = []

    def _compute_dx(s_tr: Decimal, s_pdm: Decimal, s_mdm: Decimal) -> Decimal:
        if s_tr == Decimal("0"):
            return Decimal("0.0")
        p_di = (s_pdm / s_tr) * Decimal("100")
        m_di = (s_mdm / s_tr) * Decimal("100")
        di_sum = p_di + m_di
        if di_sum == Decimal("0"):
            return Decimal("0.0")
        return (abs(p_di - m_di) / di_sum) * Decimal("100")

    dx_list.append(_compute_dx(smooth_tr, smooth_plus_dm, smooth_minus_dm))

    for i in range(period + 1, n):
        smooth_tr = smooth_tr - (smooth_tr / dec_period) + tr_list[i]
        smooth_plus_dm = smooth_plus_dm - (smooth_plus_dm / dec_period) + plus_dm[i]
        smooth_minus_dm = smooth_minus_dm - (smooth_minus_dm / dec_period) + minus_dm[i]
        dx_list.append(_compute_dx(smooth_tr, smooth_plus_dm, smooth_minus_dm))

    # Step 3: Wilder's smoothing of DX to obtain ADX
    adx_result: list[Decimal] = [Decimal("0.0")] * (2 * period - 1)
    if len(dx_list) < period:
        return [Decimal("0.0")] * n

    current_adx = sum(dx_list[:period]) / dec_period
    adx_result.append(current_adx.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    for k in range(period, len(dx_list)):
        current_adx = (current_adx * dec_period_minus_1 + dx_list[k]) / dec_period
        adx_result.append(current_adx.quantize(PRECISION, rounding=ROUND_HALF_EVEN))

    return adx_result
