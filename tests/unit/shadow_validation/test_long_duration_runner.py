"""
Unit tests for LongDurationShadowRunner (Phase 17 PS-50, PS-51, PS-52).
Tests multi-session candle stream ingestion, performance telemetry, and evidence package generation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.shadow_validation.enums import DataSourceType
from alphaforge.shadow_validation.long_duration_runner import LongDurationShadowRunner


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


def test_long_duration_runner_stream(sample_contract: ContractMaster) -> None:
    runner = LongDurationShadowRunner(
        contract=sample_contract,
        data_source_type=DataSourceType.SYNTHETIC,
        git_sha="0060d39",
    )

    base_time = datetime(2026, 9, 12, 3, 45, tzinfo=UTC)
    candles = [
        MarketCandle(
            symbol="NIFTY",
            instrument_type=InstrumentType.FUTURES,
            contract_id="NIFTY26SEPFUT",
            exchange_timestamp=base_time + timedelta(minutes=i),
            received_timestamp=base_time + timedelta(minutes=i),
            timeframe="1m",
            open=Decimal("24500.00") + Decimal(str(i)),
            high=Decimal("24510.00") + Decimal(str(i)),
            low=Decimal("24490.00") + Decimal(str(i)),
            close=Decimal("24505.00") + Decimal(str(i)),
            volume=1500,
            source="SYNTHETIC",
            is_closed=True,
        )
        for i in range(20)
    ]

    evidence = runner.run_stream(candles)
    assert evidence.run_id.startswith("PHASE17-SYNTHETIC-")
    assert evidence.decisions_count == 20
    assert evidence.valid_events_count == 20
    assert len(evidence.manifest_hash) == 64
    assert evidence.certification_levels["level_a"] == "PASS"
    assert evidence.certification_levels["level_b"] == "PASS"
    # Level C is PENDING for synthetic tests
    assert evidence.certification_levels["level_c"] == "PENDING"
