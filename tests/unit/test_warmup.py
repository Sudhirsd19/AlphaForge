"""
Unit tests for Historical Data Warmup Service (Upgrade 1).
Verifies that warmup correctly primes multi-timeframe candle buffers
to eliminate cold-start lookback delay.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from alphaforge.data.enums import InstrumentType
from alphaforge.data.warmup import warmup_historical_candles


def test_warmup_historical_candles_from_mock_fetch(tmp_path: Path) -> None:
    # 3 days of synthetic 1m data (09:15 to 15:30 IST)
    mock_candles = []
    base = datetime(2026, 9, 21, 3, 45, tzinfo=UTC)  # 09:15 IST
    for i in range(375):  # 1 full day of 1m bars
        t = base + timedelta(minutes=i)
        p = Decimal("23500.00") + Decimal(i)
        ts_ist = (t + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        mock_candles.append([ts_ist, float(p), float(p + 5), float(p - 5), float(p + 2), 1000, 50000])

    with patch("alphaforge.data.warmup.fetch_upstox_1m_candles", return_value=mock_candles):
        c3, c15 = warmup_historical_candles(
            instrument_key="NSE_FO|48704",
            symbol="NIFTY",
            contract_id="NIFTY26OCTFUT",
            instrument_type=InstrumentType.FUTURES,
            token="test-token",
            lookback_days=1,
            cache_dir=tmp_path,
        )

    # 375 1m bars / 3 = 125 3m bars
    assert len(c3) == 125
    # 375 1m bars / 15 = 25 15m bars
    assert len(c15) == 25
    assert c3[0].is_closed is True
    assert c15[0].is_closed is True
    assert len(c15) >= 21  # Sufficient to calculate EMA-21 immediately!


def test_warmup_fallback_to_cache_when_api_fails(tmp_path: Path) -> None:
    # Create cached file
    mock_candles = []
    base = datetime(2026, 9, 21, 3, 45, tzinfo=UTC)
    for i in range(75):  # 75 bars = 25 3m bars, 5 15m bars
        t = base + timedelta(minutes=i)
        p = Decimal("23500.00") + Decimal(i)
        ts_ist = (t + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        mock_candles.append([ts_ist, float(p), float(p + 5), float(p - 5), float(p + 2), 1000, 50000])

    cache_file = tmp_path / "NSE_FO_48704_cached_1m.json"
    import json
    cache_file.write_text(json.dumps(mock_candles), encoding="utf-8")

    with patch("alphaforge.data.warmup.fetch_upstox_1m_candles", side_effect=RuntimeError("API Network Down")):
        c3, c15 = warmup_historical_candles(
            instrument_key="NSE_FO|48704",
            symbol="NIFTY",
            contract_id="NIFTY26OCTFUT",
            instrument_type=InstrumentType.FUTURES,
            cache_dir=tmp_path,
        )

    assert len(c3) == 25
    assert len(c15) == 5
