"""
Unit tests for MarketStreamValidator (Phase 17 PS-39, PS-40, PS-41).
Tests multi-dimensional contract validation, session boundaries, clock drift, sequence gaps,
duplicates, and out-of-order timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.shadow_validation.enums import DataSourceType, MarketDataAnomalyType
from alphaforge.shadow_validation.market_stream_validator import MarketStreamValidator
from alphaforge.shadow_validation.models import MarketStreamEvent


@pytest.fixture
def sample_contract() -> ContractMaster:
    return ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        instrument_type=InstrumentType.FUTURES,
        expiry_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        lot_size=50,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        price_decimal_places=2,
        currency="INR",
        settlement_type=SettlementType.CASH,
        status=ContractStatus.ACTIVE,
        data_source="NSE_MASTER",
    )


def test_contract_metadata_validation(sample_contract: ContractMaster) -> None:
    validator = MarketStreamValidator(
        contract=sample_contract, expected_data_source=DataSourceType.SYNTHETIC
    )
    assert validator.validate_contract_metadata() is True

    # Corrupt contract
    bad_contract = sample_contract.model_copy(update={"exchange": "BSE"})
    bad_validator = MarketStreamValidator(
        contract=bad_contract, expected_data_source=DataSourceType.SYNTHETIC
    )
    assert bad_validator.validate_contract_metadata() is False

    with pytest.raises(DataIntegrityError):
        bad_validator.validate_event(
            MarketCandle(
                symbol="NIFTY",
                instrument_type=InstrumentType.FUTURES,
                contract_id="NIFTY26SEPFUT",
                exchange_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
                received_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
                timeframe="1m",
                open=Decimal("24500.00"),
                high=Decimal("24520.00"),
                low=Decimal("24490.00"),
                close=Decimal("24510.00"),
                volume=1000,
                source="SYNTHETIC",
                is_closed=True,
            )
        )


def test_anomaly_detection_sequence_gap_and_duplicate(sample_contract: ContractMaster) -> None:
    validator = MarketStreamValidator(
        contract=sample_contract, expected_data_source=DataSourceType.SYNTHETIC
    )
    base_ts = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)

    evt1 = MarketStreamEvent(
        event_id="EVT-1",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=1,
        exchange_timestamp=base_ts,
        ingestion_timestamp=base_ts,
        processing_timestamp=base_ts,
        open_price=Decimal("24500.00"),
        high_price=Decimal("24520.00"),
        low_price=Decimal("24490.00"),
        close_price=Decimal("24510.00"),
        volume=1000,
        data_source=DataSourceType.SYNTHETIC,
    )
    res1 = validator.validate_event(evt1)
    assert len(res1.anomalies) == 0

    # Duplicate
    res_dup = validator.validate_event(evt1)
    assert MarketDataAnomalyType.DUPLICATE in res_dup.anomalies

    # Sequence Gap (jump from 1 to 5)
    evt_gap = MarketStreamEvent(
        event_id="EVT-5",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=5,
        exchange_timestamp=base_ts + timedelta(minutes=1),
        ingestion_timestamp=base_ts + timedelta(minutes=1),
        processing_timestamp=base_ts + timedelta(minutes=1),
        open_price=Decimal("24510.00"),
        high_price=Decimal("24530.00"),
        low_price=Decimal("24505.00"),
        close_price=Decimal("24525.00"),
        volume=1200,
        data_source=DataSourceType.SYNTHETIC,
    )
    res_gap = validator.validate_event(evt_gap)
    assert MarketDataAnomalyType.SEQUENCE_GAP in res_gap.anomalies
