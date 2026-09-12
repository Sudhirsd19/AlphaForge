"""
Unit tests for alphaforge.backtest.datasets.
Verifies dataset checksum determinism, ordering enforcement, and survivorship diagnostics.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.backtest.datasets import (
    BacktestDataset,
    compute_dataset_checksum,
)
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle


def _create_candle(ts: datetime, close_p: Decimal, vol: int = 100) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=close_p,
        high=close_p + Decimal("5"),
        low=close_p - Decimal("5"),
        close=close_p,
        volume=vol,
        open_interest=50000,
        source="NSE",
    )


def test_checksum_determinism() -> None:
    """Verify compute_dataset_checksum is bit-for-bit deterministic."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _create_candle(t0, Decimal("24000"))
    c2 = _create_candle(t0 + timedelta(minutes=3), Decimal("24010"))

    cs1 = compute_dataset_checksum([c1, c2])
    cs2 = compute_dataset_checksum([c1, c2])
    assert cs1 == cs2
    assert len(cs1) == 64

    # Mutate price by 1 point -> completely different checksum
    c2_mutated = _create_candle(t0 + timedelta(minutes=3), Decimal("24011"))
    cs_mutated = compute_dataset_checksum([c1, c2_mutated])
    assert cs1 != cs_mutated

    # Mutate volume -> completely different checksum
    c2_vol = _create_candle(t0 + timedelta(minutes=3), Decimal("24010"), vol=101)
    cs_vol = compute_dataset_checksum([c1, c2_vol])
    assert cs1 != cs_vol


def test_dataset_ordering_and_duplicates() -> None:
    """Verify dataset enforces chronological order and rejects duplicates."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _create_candle(t0, Decimal("24000"))
    c2 = _create_candle(t0 + timedelta(minutes=3), Decimal("24010"))
    c3 = _create_candle(t0 + timedelta(minutes=6), Decimal("24020"))

    # Sorting reorders out-of-order inputs correctly
    ds = BacktestDataset("TEST_DS", [c2, c1, c3])
    assert len(ds) == 3
    assert ds[0].exchange_timestamp == t0
    assert ds[1].exchange_timestamp == t0 + timedelta(minutes=3)
    assert ds[2].exchange_timestamp == t0 + timedelta(minutes=6)

    # Reject duplicate timestamps
    c1_dup = _create_candle(t0, Decimal("24005"))
    with pytest.raises(BacktestValidationError, match="Duplicate candle timestamp"):
        BacktestDataset("TEST_DUP", [c1, c1_dup])


def test_dataset_slicing() -> None:
    """Verify slice_by_time partitions without leaking outside bounds."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [
        _create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000") + Decimal(i))
        for i in range(10)
    ]
    ds = BacktestDataset("DS_10", candles)

    t_start = t0 + timedelta(minutes=6)
    t_end = t0 + timedelta(minutes=18)
    slice_ds = ds.slice_by_time(t_start, t_end)

    assert len(slice_ds) == 5
    assert slice_ds[0].exchange_timestamp == t_start
    assert slice_ds[-1].exchange_timestamp == t_end


def test_survivorship_diagnostics() -> None:
    """Verify survivorship bias telemetry."""
    empty_ds = BacktestDataset("EMPTY_DS", [])
    warns = empty_ds.diagnose_survivorship()
    assert any("zero records" in w for w in warns)

    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c1 = _create_candle(t0, Decimal("24000"))
    ds = BacktestDataset("ACTIVE_DS", [c1])
    warns = ds.diagnose_survivorship(known_active_contracts=["NIFTY-SPOT", "NIFTY-FUT-EXP"])
    assert any("missing from historical data" in w for w in warns)
