"""
Unit tests for Market Data Provenance Enforcement and Anti-Fraud Protection (Phase 17C).
Verifies that synthetic data, self-declared tags, and missing external tokens CANNOT pass Level C.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.shadow_validation.certification_reporter import CertificationReporter
from alphaforge.shadow_validation.enums import (
    CertificationLevelStatus,
    CertificationVerdict,
    DataSourceType,
    MarketDataAnomalyType,
)
from alphaforge.shadow_validation.long_duration_runner import LongDurationShadowRunner
from alphaforge.shadow_validation.market_data_adapter import (
    AuthorizedLiveStreamAdapter,
    ProvenanceVerifier,
)
from alphaforge.shadow_validation.market_stream_validator import MarketStreamValidator
from alphaforge.shadow_validation.models import (
    CertificationConfig,
    FeedProvenanceToken,
    MarketStreamEvent,
)


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


def test_self_declared_provenance_rejected(sample_contract: ContractMaster) -> None:
    """Proves that a raw MarketCandle claiming REAL_MARKET_SHADOW is demoted and flagged."""
    validator = MarketStreamValidator(
        contract=sample_contract,
        expected_data_source=DataSourceType.REAL_MARKET_SHADOW,
    )
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    fake_live_candle = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=now,
        received_timestamp=now,
        timeframe="1m",
        open=Decimal("24500.00"),
        high=Decimal("24520.00"),
        low=Decimal("24490.00"),
        close=Decimal("24510.00"),
        volume=1000,
        source="REAL_MARKET_SHADOW",  # Self-declared string
        is_closed=True,
    )

    event = validator.validate_event(fake_live_candle)
    assert MarketDataAnomalyType.UNVERIFIED_PROVENANCE in event.anomalies
    assert event.data_source == DataSourceType.SYNTHETIC


def test_synthetic_candles_cannot_pass_level_c(sample_contract: ContractMaster) -> None:
    """
    Proves that synthetic candles CANNOT obtain Level C PASS even if caller
    specifies REAL_MARKET_SHADOW.
    """
    config = CertificationConfig(
        minimum_duration_seconds=10,
        minimum_market_sessions=1,
        minimum_valid_market_events=5,
        minimum_shadow_decisions=1,
    )
    runner = LongDurationShadowRunner(
        contract=sample_contract,
        config=config,
        data_source_type=DataSourceType.REAL_MARKET_SHADOW,
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
            open=Decimal("24500.00"),
            high=Decimal("24510.00"),
            low=Decimal("24490.00"),
            close=Decimal("24505.00"),
            volume=1500,
            source="REAL_MARKET_SHADOW",  # Self-declared
            is_closed=True,
        )
        for i in range(10)
    ]

    evidence = runner.run_stream(candles)
    assert evidence.data_source_type == DataSourceType.SYNTHETIC
    assert evidence.provenance_verified_events_count == 0
    assert evidence.certification_levels["level_c"] == "PENDING"

    report = CertificationReporter.generate_report(
        evidence=evidence,
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.PASS,
        level_c=CertificationLevelStatus.PENDING,
    )
    assert report.final_verdict == CertificationVerdict.PHASE_17_BLOCKED


def test_provenance_verifier_tamper_detection() -> None:
    """Tests cryptographic signature validation in ProvenanceVerifier."""
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    raw_payload = b'{"msg":"tick","ltp":24500}'
    import hashlib

    raw_hash = hashlib.sha256(raw_payload).hexdigest()

    sig = ProvenanceVerifier.generate_attestation_hmac(
        provider="DHAN_HQ_STREAM",
        session_id="SESS-12345",
        raw_payload_hash=raw_hash,
    )

    token = FeedProvenanceToken(
        provider="DHAN_HQ_STREAM",
        provider_authenticated=True,
        connection_session_id="SESS-12345",
        source_timestamp=now,
        raw_payload_hash=raw_hash,
        alpha_forge_attestation_hmac=sig,
        is_live_external=True,
    )

    event = MarketStreamEvent(
        event_id="EVT-LIVE-1",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=1,
        exchange_timestamp=now,
        ingestion_timestamp=now,
        processing_timestamp=now,
        open_price=Decimal("24500.00"),
        high_price=Decimal("24510.00"),
        low_price=Decimal("24490.00"),
        close_price=Decimal("24505.00"),
        volume=100,
        data_source=DataSourceType.REAL_MARKET_SHADOW,
        provenance=token,
    )
    is_valid, reason = ProvenanceVerifier.verify_provenance(event)
    assert is_valid is True
    assert reason is None

    # Tampered signature
    bad_token = token.model_copy(update={"alpha_forge_attestation_hmac": "bad_signature"})
    bad_event = event.model_copy(update={"provenance": bad_token})
    is_valid_bad, reason_bad = ProvenanceVerifier.verify_provenance(bad_event)
    assert is_valid_bad is False
    assert "CORRUPTED_ATTESTATION" in str(reason_bad)


def test_live_adapter_fail_closed_missing_credentials(sample_contract: ContractMaster) -> None:
    """Proves AuthorizedLiveStreamAdapter fails closed when live endpoint/auth is missing."""
    adapter = AuthorizedLiveStreamAdapter(
        contract=sample_contract,
        provider_name="DHAN_HQ_STREAM",
        feed_endpoint=None,
        auth_token=None,
    )
    with pytest.raises(ConnectionError, match="Live market data endpoint or auth token is missing"):
        adapter.connect()
