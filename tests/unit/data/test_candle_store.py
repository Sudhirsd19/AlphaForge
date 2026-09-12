"""
Unit tests for CandleStore and Strategy Engine bridge contracts.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.data.store import CandleStore


def _create_market_candle(
    ts: datetime,
    close_p: Decimal = Decimal("25000.00"),
    is_closed: bool = True,
    timeframe: str = "3m",
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe=timeframe,
        open=close_p - Decimal("10.00"),
        high=close_p + Decimal("20.00"),
        low=close_p - Decimal("20.00"),
        close=close_p,
        volume=5000,
        source="TEST",
        is_closed=is_closed,
    )


def test_candle_store_add_and_get() -> None:
    """CandleStore stores candles and retrieves them chronologically."""
    store = CandleStore()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)

    c0 = _create_market_candle(t0, Decimal("25000.00"))
    c1 = _create_market_candle(t1, Decimal("25020.00"))

    # Add out of order
    store.add_candle(c1)
    store.add_candle(c0)

    retrieved = store.get_candles("NIFTY", "3m")
    assert len(retrieved) == 2
    assert retrieved[0].exchange_timestamp == t0
    assert retrieved[1].exchange_timestamp == t1
    assert store.get_latest_candle("NIFTY", "3m") == c1


def test_candle_store_time_range_query() -> None:
    """CandleStore accurately filters by start_time and end_time."""
    store = CandleStore()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    candles = [
        _create_market_candle(base_t + timedelta(minutes=3 * i), Decimal(25000 + i * 10))
        for i in range(5)
    ]
    store.add_candles(candles)

    # Filter between index 1 and 3 inclusive
    filtered = store.get_candles(
        "NIFTY",
        "3m",
        start_time=base_t + timedelta(minutes=3),
        end_time=base_t + timedelta(minutes=9),
    )
    assert len(filtered) == 3
    assert filtered[0].exchange_timestamp == base_t + timedelta(minutes=3)
    assert filtered[-1].exchange_timestamp == base_t + timedelta(minutes=9)


def test_candle_store_duplicate_and_conflict() -> None:
    """Identical duplicate is idempotent; differing duplicate raises DataIntegrityError."""
    store = CandleStore()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    c0 = _create_market_candle(t0, Decimal("25000.00"))

    store.add_candle(c0)
    # Add identical duplicate: must not duplicate or raise
    store.add_candle(c0)
    assert store.count("NIFTY", "3m") == 1

    # Add conflicting candle at same timestamp
    c_conflict = _create_market_candle(t0, Decimal("25050.00"))
    with pytest.raises(DataIntegrityError, match="Conflicting candle insertion"):
        store.add_candle(c_conflict)


def test_candle_store_forming_candle_isolation() -> None:
    """Forming candle is stored separately and never returned in closed candle queries."""
    store = CandleStore()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)

    c0_closed = _create_market_candle(t0, Decimal("25000.00"), is_closed=True)
    c1_forming = _create_market_candle(t1, Decimal("25020.00"), is_closed=False)

    store.add_candle(c0_closed)
    store.add_candle(c1_forming)

    # get_candles only returns closed
    closed_list = store.get_candles("NIFTY", "3m")
    assert len(closed_list) == 1
    assert closed_list[0] == c0_closed

    # Forming store holds c1_forming
    assert store.get_forming_candle("NIFTY", "3m") == c1_forming


def test_candle_store_strategy_execution_input_bridge() -> None:
    """
    Verify get_strategy_execution_input produces exact Phase 1 sequence:
    index [0] = forming candle (is_closed=False)
    index [1] = latest closed trigger candle
    index [2..N] = older closed candles in reverse order
    """
    store = CandleStore()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    for i in range(5):
        store.add_candle(
            _create_market_candle(
                base_t + timedelta(minutes=3 * i), Decimal(25000 + i * 10), is_closed=True
            )
        )

    t_forming = base_t + timedelta(minutes=15)
    forming = _create_market_candle(t_forming, Decimal("25060.00"), is_closed=False)
    store.add_candle(forming)

    strat_input = store.get_strategy_execution_input("NIFTY", "3m", count=5)
    assert len(strat_input) == 6  # 1 forming + 5 closed
    # Index [0] is forming
    assert strat_input[0].is_closed is False
    assert strat_input[0].timestamp == t_forming

    # Index [1] is latest closed (bar 4 at 09:27)
    assert strat_input[1].is_closed is True
    assert strat_input[1].timestamp == base_t + timedelta(minutes=12)
    assert strat_input[1].close == Decimal("25040.00")

    # Index [2] is prior closed (bar 3 at 09:24)
    assert strat_input[2].timestamp == base_t + timedelta(minutes=9)
    assert strat_input[2].close == Decimal("25030.00")


def test_candle_store_strategy_confirmation_input_bridge() -> None:
    """Verify get_strategy_confirmation_input returns chronological closed candles up to max_ts."""
    store = CandleStore()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    for i in range(10):
        store.add_candle(
            _create_market_candle(
                base_t + timedelta(minutes=15 * i),
                Decimal(25000 + i * 20),
                is_closed=True,
                timeframe="15m",
            )
        )

    # Cut off at bar 5 (09:15 + 75m = 10:30)
    cutoff_ts = base_t + timedelta(minutes=15 * 5)
    conf_input = store.get_strategy_confirmation_input(
        "NIFTY", "15m", max_timestamp=cutoff_ts, count=4
    )

    assert len(conf_input) == 4
    # Chronological ordering preserved
    assert conf_input[0].timestamp < conf_input[1].timestamp < conf_input[2].timestamp
    assert conf_input[-1].timestamp == cutoff_ts
