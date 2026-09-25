"""
AlphaForge Historical Market Data Warmup Service.
Provides deterministic pre-session and startup warmup of multi-timeframe candle buffers
to eliminate cold-start lookback delay (REJECT_INSUFFICIENT_HISTORY and REJECT_MISSING_TIMEFRAME).
"""

from __future__ import annotations

import contextlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from alphaforge.core.models import Candle
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from scripts.run_real_data_backtest import fetch_upstox_1m_candles, resample_1m_to_interval

if TYPE_CHECKING:
    from alphaforge.strategy.engine import DeterministicStrategyEngine

logger = logging.getLogger("alphaforge.data.warmup")


def warmup_historical_candles(
    instrument_key: str,
    symbol: str = "NIFTY",
    contract_id: str = "NIFTY26OCTFUT",
    instrument_type: InstrumentType = InstrumentType.FUTURES,
    token: str = "",
    lookback_days: int = 5,
    cache_dir: Path | None = None,
) -> tuple[list[Candle], list[Candle]]:
    """
    Fetch and resample multi-day historical candles to prime 3m and 15m strategy buffers.

    Returns:
        (candles_3m, candles_15m) as immutable strategy Candle instances.
    """
    now = datetime.now(UTC)
    from_date = now - timedelta(days=lookback_days)
    to_date = now

    raw_candles: list[list] = []

    # 1. Attempt Upstox API fetch
    try:
        raw_candles = fetch_upstox_1m_candles(
            instrument_key=instrument_key,
            from_date=from_date,
            to_date=to_date,
            token=token,
            cache_dir=cache_dir,
        )
    except Exception as e:
        logger.warning("Upstox historical API fetch failed during warmup: %s", e)

    # 2. Fallback to cached historical file if API returned nothing
    if not raw_candles and cache_dir and cache_dir.exists():
        safe_key = instrument_key.replace("|", "_").replace(" ", "_")
        matching = sorted(cache_dir.glob(f"*{safe_key}*1m.json"), reverse=True)
        if not matching:
            # Also search for contract_id
            matching = sorted(cache_dir.glob(f"*{contract_id}*1m.json"), reverse=True)

        for cf in matching:
            try:
                loaded = json.loads(cf.read_text(encoding="utf-8"))
                if loaded and isinstance(loaded, list):
                    raw_candles = loaded
                    logger.info("Warmup fallback loaded %d candles from disk cache: %s", len(raw_candles), cf.name)
                    break
            except Exception as e:
                logger.warning("Could not read historical cache %s: %s", cf, e)

    if not raw_candles:
        logger.warning("No historical candles available for warmup of %s.", instrument_key)
        return [], []

    # 3. Resample to 3m and 15m regular trading session bars
    mc_3m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol=symbol,
        contract_id=contract_id,
        instrument_type=instrument_type,
        interval_minutes=3,
        filter_regular_session=True,
    )
    mc_15m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol=symbol,
        contract_id=contract_id,
        instrument_type=instrument_type,
        interval_minutes=15,
        filter_regular_session=True,
    )

    strat_3m = [c.to_strategy_candle() for c in mc_3m]
    strat_15m = [c.to_strategy_candle() for c in mc_15m]

    logger.info(
        "Historical warmup complete: %d 3m bars, %d 15m bars prepared for %s.",
        len(strat_3m),
        len(strat_15m),
        instrument_key,
    )
    return strat_3m, strat_15m
