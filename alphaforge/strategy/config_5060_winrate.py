"""
AlphaForge 50-60% Win Rate Strategy Configuration.
Optimized to filter out weak trades while maintaining reasonable frequency.

Based on 60-day backtest results:
- 32 total trades
- 13 wins (40.6%)
- 19 losses (59.4%)

Goal: Convert 6-7 losing trades to wins = 50-60% win rate
"""

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Strategy5060WinRateConfig(BaseModel):
    """
    Configuration targeting 50-60% win rate with 20-30 trades in 60 days.

    Analysis of losing trades shows patterns:
    1. Too early entries (breakout lookback too short)
    2. Weak volume confirmation
    3. Choppy market entries

    Target adjustments:
    - Breakout: 18 bars (vs 15) - stronger levels
    - Volume: 1.25x (vs 1.10x) - stronger participation
    - RSI: Medium bands - avoid extreme overbought/oversold
    - Stop: 1.2x ATR - slightly wider to avoid whipsaw
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = "AF_ORB_MOMENTUM_V1"
    strategy_version: str = "1.4.0-50-60-WR"
    symbol: str = "NIFTY"
    exec_timeframe: str = "3m"
    conf_timeframe: str = "15m"

    # Trend Regime Parameters
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21

    # Range & Breakout - MODERATE (18 vs 15 earlier)
    breakout_lookback: int = 18

    # Confirmation Candle Geometry - MODERATE
    min_body_ratio: Decimal = Decimal("0.50")      # Up from 0.45
    min_close_location_ratio: Decimal = Decimal("0.68")  # Slight improvement

    # Relative Volume - MODERATELY STRONGER
    volume_lookback: int = 20
    min_relative_volume: Decimal = Decimal("1.25")  # Up from 1.10

    # Momentum Parameters - MEDIUM BANDS (not too wide/not too narrow)
    rsi_period: int = 14
    rsi_long_min: Decimal = Decimal("48.0")  # Balanced
    rsi_long_max: Decimal = Decimal("78.0")  # Balanced
    rsi_short_min: Decimal = Decimal("22.0")  # Balanced
    rsi_short_max: Decimal = Decimal("52.0")  # Balanced

    # Volatility Parameters - PROTECTION AGAINST CHOPPY MARKETS
    atr_period: int = 14
    atr_stop_multiplier: Decimal = Decimal("1.2")  # Wider than 1.0
    atr_min_pct: Decimal = Decimal("0.0004")       # Moderate
    atr_max_pct: Decimal = Decimal("0.0160")       # Moderate

    # Stop & Target Bounds
    swing_stop_lookback: int = 2
    min_risk_distance_pct: Decimal = Decimal("0.0010")  # Back to baseline
    max_risk_distance_pct: Decimal = Decimal("0.0300")  # Back to baseline
    target_risk_multiple: Decimal = Decimal("2.0")       # Maintain 1:2 R:R

    # Stale Data Threshold
    max_stale_seconds: int = 195

    # Market Regime Filter - LIGHT
    enable_regime_filter: bool = False
    min_adx_threshold: Decimal = Decimal("18.0")    # Very light
    min_ema_spread_pct: Decimal = Decimal("0.0010")  # Light trend

    # Afternoon Entry Cutoff - DISABLED (for trade frequency)
    enable_entry_cutoff: bool = False
    entry_cutoff_time_ist: str = "15:30"

    def compute_config_hash(self) -> str:
        """Produce deterministic SHA-256 hash."""
        raw_dict = self.model_dump()
        serializable_dict = {
            k: str(v) if isinstance(v, Decimal) else v for k, v in raw_dict.items()
        }
        canonical_json = json.dumps(serializable_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


PREDICTED_IMPROVEMENT = """
╔═══════════════════════════════════════════════════════════════════╗
║                EXPECTED IMPROVEMENT FROM 40.6% TO 50-60%         ║
╠═══════════════════════════════════════════════════════════════════╣
║ Current (Balanced v1.3.0):                                       ║
║ • Win Rate: 40.6% (13/32)                                        ║
║ • Average Win: ₹6,492                                            ║
║ • Average Loss: ₹2,257                                           ║
║ • Payoff Ratio: 2.88                                             ║
╠═══════════════════════════════════════════════════════════════════╣
║ Expected (50-60% v1.4.0):                                        ║
║ • Win Rate: 50-60% (15-19/30)                                    ║
║ • Trade Count: 25-30 (filter out 2-7 weak signals)               ║
║ • Same Payoff Ratio maintained                                   ║
║ • Return: ~5-6% (up from 4.15%)                                  ║
║                                                                   ║
║ Filtered likely losing trades:                                   ║
║ 1. Low volume breakouts (<1.25x)                                 ║
║ 2. Weak candle geometry (body <0.50, close loc <0.68)            ║
║ 3. Extreme RSI (>78 or <22)                                      ║
║ 4. Choppy market entries (volatile whipsaw)                      ║
╚═══════════════════════════════════════════════════════════════════╝
"""

if __name__ == "__main__":
    config = Strategy5060WinRateConfig()
    print(f"Strategy: {config.strategy_id} v{config.strategy_version}")
    print(f"Config Hash: {config.compute_config_hash()}")
    print(PREDICTED_IMPROVEMENT)
