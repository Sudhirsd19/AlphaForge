"""
Unit tests for canonical MarketCandle schema, precision, and immutability.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle


def test_market_candle_valid_construction() -> None:
    """Verify that a canonical MarketCandle with all 15 fields instantiates correctly."""
    candle = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=datetime(2026, 9, 11, 9, 15, tzinfo=UTC),
        received_timestamp=datetime(2026, 9, 11, 9, 15, 1, tzinfo=UTC),
        timeframe="3m",
        open=Decimal("25000.00"),
        high=Decimal("25050.00"),
        low=Decimal("24980.00"),
        close=Decimal("25030.00"),
        volume=15000,
        open_interest=500000,
        source="MOCK_VENUE",
        quality_status=DataQualityStatus.VALID,
        data_version=1,
        is_closed=True,
    )

    assert candle.symbol == "NIFTY"
    assert candle.instrument_type == InstrumentType.FUTURES
    assert candle.contract_id == "NIFTY26SEPFUT"
    assert candle.exchange_timestamp == datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    assert candle.received_timestamp == datetime(2026, 9, 11, 9, 15, 1, tzinfo=UTC)
    assert candle.timeframe == "3m"
    assert candle.open == Decimal("25000.00")
    assert candle.high == Decimal("25050.00")
    assert candle.low == Decimal("24980.00")
    assert candle.close == Decimal("25030.00")
    assert candle.volume == 15000
    assert candle.open_interest == 500000
    assert candle.source == "MOCK_VENUE"
    assert candle.quality_status == DataQualityStatus.VALID
    assert candle.data_version == 1
    assert candle.is_closed is True


def test_market_candle_immutability() -> None:
    """Verify that MarketCandle instances are strictly frozen/immutable."""
    candle = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=datetime(2026, 9, 11, 9, 15, tzinfo=UTC),
        received_timestamp=datetime(2026, 9, 11, 9, 15, tzinfo=UTC),
        timeframe="3m",
        open=Decimal("25000.00"),
        high=Decimal("25020.00"),
        low=Decimal("24990.00"),
        close=Decimal("25010.00"),
        volume=0,
        source="SPOT_FEED",
    )

    with pytest.raises(ValidationError):
        candle.close = Decimal("25050.00")


def test_market_candle_symbol_validation() -> None:
    """Verify symbol must be non-empty and uppercase."""
    with pytest.raises(DataIntegrityError, match="uppercase"):
        MarketCandle(
            symbol="nifty",  # Lowercase rejected
            instrument_type=InstrumentType.INDEX,
            contract_id="NIFTY-SPOT",
            exchange_timestamp=datetime(2026, 9, 11, 9, 15, tzinfo=UTC),
            received_timestamp=datetime(2026, 9, 11, 9, 15, tzinfo=UTC),
            timeframe="3m",
            open=Decimal("25000.00"),
            high=Decimal("25020.00"),
            low=Decimal("24990.00"),
            close=Decimal("25010.00"),
            volume=0,
            source="SPOT_FEED",
        )


def test_market_candle_bridge_to_strategy_candle() -> None:
    """Verify to_strategy_candle() converts cleanly to Phase 1 Candle model."""
    mc = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=datetime(2026, 9, 11, 9, 18, tzinfo=UTC),
        received_timestamp=datetime(2026, 9, 11, 9, 18, 1, tzinfo=UTC),
        timeframe="3m",
        open=Decimal("25010.00"),
        high=Decimal("25060.00"),
        low=Decimal("25005.00"),
        close=Decimal("25050.00"),
        volume=12000,
        open_interest=498000,
        source="FEED",
        is_closed=True,
    )

    sc = mc.to_strategy_candle()
    assert sc.timestamp == mc.exchange_timestamp
    assert sc.open == mc.open
    assert sc.high == mc.high
    assert sc.low == mc.low
    assert sc.close == mc.close
    assert sc.volume == mc.volume
    assert sc.open_interest == mc.open_interest
    assert sc.is_closed is True
