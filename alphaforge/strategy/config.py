"""
AlphaForge Strategy Configuration.
Defines immutable strategy hyperparameters and canonical serialization hashing.
"""

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StrategyConfig(BaseModel):
    """
    Immutable hyperparameter configuration for the deterministic strategy.
    """
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = "AF_ORB_MOMENTUM_V1"
    strategy_version: str = "1.0.0"
    symbol: str = "NIFTY"
    exec_timeframe: str = "3m"
    conf_timeframe: str = "15m"

    # Trend Regime Parameters (Higher Timeframe)
    trend_ema_fast: int = 9
    trend_ema_slow: int = 21

    # Range & Breakout Parameters (Execution Timeframe)
    breakout_lookback: int = 20

    # Confirmation Candle Geometry
    min_body_ratio: Decimal = Decimal("0.50")
    min_close_location_ratio: Decimal = Decimal("0.70")

    # Relative Volume Parameters
    volume_lookback: int = 20
    min_relative_volume: Decimal = Decimal("1.20")

    # Momentum Parameters (RSI)
    rsi_period: int = 14
    rsi_long_min: Decimal = Decimal("50.0")
    rsi_long_max: Decimal = Decimal("75.0")
    rsi_short_min: Decimal = Decimal("25.0")
    rsi_short_max: Decimal = Decimal("50.0")

    # Volatility Parameters (ATR)
    atr_period: int = 14
    atr_stop_multiplier: Decimal = Decimal("1.0")
    atr_min_pct: Decimal = Decimal("0.0005")  # 0.05% of price
    atr_max_pct: Decimal = Decimal("0.0150")  # 1.50% of price

    # Stop & Target Bounds
    min_risk_distance_pct: Decimal = Decimal("0.0010")  # 0.10% of price
    max_risk_distance_pct: Decimal = Decimal("0.0300")  # 3.00% of price
    target_risk_multiple: Decimal = Decimal("2.0")      # 1:2 R:R

    # Stale Data Threshold (Seconds)
    max_stale_seconds: int = 195  # 3m (180s) + 15s grace

    def compute_config_hash(self) -> str:
        """
        Produce a deterministic SHA-256 hash of the canonical JSON representation.
        Ensures exact reproducibility across different runs and systems.
        """
        raw_dict = self.model_dump()
        # Convert Decimal values to standardized strings for exact JSON hashing
        serializable_dict = {
            k: str(v) if isinstance(v, Decimal) else v
            for k, v in raw_dict.items()
        }
        canonical_json = json.dumps(serializable_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
