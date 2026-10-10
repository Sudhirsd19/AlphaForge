"""
AlphaForge Balanced Strategy Configuration.
Optimized for 10-20 trades/month with 50-60% win rate.

Goal: Balance between trade frequency and win rate
- Looser filters than baseline (more signals)
- Still maintains quality control
- Target: 50-60% win rate, 10-20 trades/month
"""

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BalancedStrategyConfig(BaseModel):
    """
    Balanced configuration for moderate frequency with decent win rate.

    Key changes from baseline (which gave 3 trades in 60 days):
    1. Reduced breakout lookback (15 vs 20) - catches moves earlier
    2. Lower volume threshold (1.1x vs 1.2x) - more signals
    3. Wider RSI bands - accepts more momentum conditions
    4. Slightly wider volatility acceptance
    5. Regime filter disabled - trades all market conditions
    6. Entry cutoff disabled - full day trading
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = "AF_ORB_MOMENTUM_V1"
    strategy_version: str = "1.3.0-BALANCED"
    symbol: str = "NIFTY"
    exec_timeframe: str = "3m"
    conf_timeframe: str = "15m"

    # Trend Regime Parameters
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21

    # Range & Breakout - REDUCED for earlier entries
    breakout_lookback: int = 15  # Down from 20

    # Confirmation Candle Geometry - RELAXED
    min_body_ratio: Decimal = Decimal("0.45")  # Down from 0.50
    min_close_location_ratio: Decimal = Decimal("0.65")  # Down from 0.70

    # Relative Volume - RELAXED
    volume_lookback: int = 20
    min_relative_volume: Decimal = Decimal("1.10")  # Down from 1.20

    # Momentum Parameters - WIDER BANDS
    rsi_period: int = 14
    rsi_long_min: Decimal = Decimal("45.0")  # Down from 50.0
    rsi_long_max: Decimal = Decimal("80.0")  # Up from 75.0
    rsi_short_min: Decimal = Decimal("20.0")  # Down from 25.0
    rsi_short_max: Decimal = Decimal("55.0")  # Up from 50.0

    # Volatility Parameters - SLIGHTLY WIDER
    atr_period: int = 14
    atr_stop_multiplier: Decimal = Decimal("1.0")
    atr_min_pct: Decimal = Decimal("0.0003")  # Down from 0.0005
    atr_max_pct: Decimal = Decimal("0.0180")  # Up from 0.0150

    # Stop & Target Bounds
    swing_stop_lookback: int = Field(
        default=2, gt=0, description="Standard swing lookback"
    )
    min_risk_distance_pct: Decimal = Decimal("0.0008")  # Down from 0.0010
    max_risk_distance_pct: Decimal = Decimal("0.0350")  # Up from 0.0300
    target_risk_multiple: Decimal = Decimal("2.0")  # Maintains 1:2 R:R

    # Stale Data Threshold
    max_stale_seconds: int = 195

    # Market Regime Filter - DISABLED for more trades
    enable_regime_filter: bool = False
    min_adx_threshold: Decimal = Decimal("20.0")
    min_ema_spread_pct: Decimal = Decimal("0.0008")

    # Afternoon Entry Cutoff - DISABLED for full day
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


COMPARISON_TABLE = """
╔═══════════════════════════════════════════════════════════════════════╗
║        PARAMETER COMPARISON: BASELINE vs BALANCED vs HIGH-WR          ║
╠═══════════════════════════════════════════════════════════════════════╣
║ Parameter              │ Baseline  │ Balanced (New) │ High WR         ║
╠═══════════════════════════════════════════════════════════════════════╣
║ Breakout Lookback      │ 20        │ 15             │ 30              ║
║ Min Body Ratio         │ 0.50      │ 0.45           │ 0.60            ║
║ Min Close Loc Ratio    │ 0.70      │ 0.65           │ 0.80            ║
║ Min Relative Volume    │ 1.20x     │ 1.10x          │ 1.50x           ║
║ RSI Long Range         │ [50, 75]  │ [45, 80]       │ [55, 70]        ║
║ RSI Short Range        │ [25, 50]  │ [20, 55]       │ [30, 45]        ║
║ Min ATR %              │ 0.05%     │ 0.03%          │ 0.10%           ║
║ Max ATR %              │ 1.50%     │ 1.80%          │ 1.20%           ║
║ Min Risk Distance      │ 0.10%     │ 0.08%          │ 0.15%           ║
║ Max Risk Distance      │ 3.00%     │ 3.50%          │ 2.50%           ║
║ Regime Filter          │ Disabled  │ Disabled       │ Enabled         ║
║ Entry Cutoff           │ Disabled  │ Disabled       │ 14:00 IST       ║
╠═══════════════════════════════════════════════════════════════════════╣
║ Expected Trades/Month  │ 1-2       │ 10-20          │ 1-3             ║
║ Expected Win Rate      │ 50-60%    │ 50-60%         │ 70-80%          ║
║ Trade Quality          │ Medium    │ Medium-High    │ Very High       ║
╚═══════════════════════════════════════════════════════════════════════╝
"""

if __name__ == "__main__":
    config = BalancedStrategyConfig()
    print(f"Strategy: {config.strategy_id} v{config.strategy_version}")
    print(f"Config Hash: {config.compute_config_hash()}")
    print(COMPARISON_TABLE)
