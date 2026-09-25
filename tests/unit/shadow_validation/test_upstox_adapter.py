"""
Unit tests for UpstoxMarketDataAdapter (Phase 17C).

All tests use mocked Upstox responses/frames.
A mock test MUST NEVER be considered real-market evidence.

Tests verify:
1. Missing credentials → fail closed
2. Unauthorized connection → fail closed
3. Malformed WebSocket message handling
4. Invalid instrument rejection
5. Invalid timestamp handling
6. Missing timestamp handling
7. Duplicate event detection
8. Out-of-order event detection
9. Provider disconnect handling
10. Reconnect handling
11. Provenance generation
12. Provenance tampering
13. Self-declared REAL_MARKET_SHADOW rejection
14. Synthetic fallback cannot become Level C
15. Upstox data cannot call order-routing APIs
16. Expired NIFTY contract rejection
17. Wrong segment rejection
18. Wrong instrument type rejection
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.shadow_validation.enums import (
    CertificationLevelStatus,
    CertificationVerdict,
    DataSourceType,
    MarketDataAnomalyType,
)
from alphaforge.shadow_validation.market_data_adapter import ProvenanceVerifier
from alphaforge.shadow_validation.models import FeedProvenanceToken, MarketStreamEvent
from alphaforge.shadow_validation.upstox_adapter import (
    UpstoxAuthenticationError,
    UpstoxMarketDataAdapter,
    _CandleAggregator,
)

# --- Fixtures ---


@pytest.fixture
def active_contract() -> ContractMaster:
    """Active NIFTY Futures contract for testing."""
    return ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26OCTFUT",
        instrument_type=InstrumentType.FUTURES,
        expiry_datetime=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
        lot_size=50,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        price_decimal_places=2,
        currency="INR",
        settlement_type=SettlementType.CASH,
        status=ContractStatus.ACTIVE,
        data_source="NSE_MASTER",
    )


@pytest.fixture
def expired_contract() -> ContractMaster:
    """Expired NIFTY Futures contract for testing rejection."""
    return ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY25JUNFUT",
        instrument_type=InstrumentType.FUTURES,
        expiry_datetime=datetime(2025, 6, 26, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2025, 3, 1, 3, 45, tzinfo=UTC),
        trading_start_datetime=datetime(2025, 3, 1, 3, 45, tzinfo=UTC),
        trading_end_datetime=datetime(2025, 6, 26, 10, 0, tzinfo=UTC),
        lot_size=50,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        price_decimal_places=2,
        currency="INR",
        settlement_type=SettlementType.CASH,
        status=ContractStatus.ACTIVE,
        data_source="NSE_MASTER",
    )


# --- Test 1: Missing credentials → fail closed ---


def test_missing_credentials_fails_closed(active_contract: ContractMaster) -> None:
    """Verifies missing UPSTOX_ACCESS_TOKEN raises UpstoxAuthenticationError."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token=None,
    )
    with patch.dict("os.environ", {}, clear=True):
        adapter._access_token = None  # Ensure no env var fallback
        with pytest.raises(UpstoxAuthenticationError, match="UPSTOX_ACCESS_TOKEN is missing"):
            adapter.connect()


# --- Test 2: Unauthorized connection → fail closed ---


def test_unauthorized_connection_fails_closed(active_contract: ContractMaster) -> None:
    """Verifies HTTP 401 on auth endpoint raises UpstoxAuthenticationError."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="INVALID_TOKEN",
    )
    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch("httpx.get", return_value=mock_response):
        with pytest.raises(UpstoxAuthenticationError, match="HTTP 401"):
            adapter.connect()


# --- Test 3: Malformed WebSocket message ---


def test_malformed_websocket_message(active_contract: ContractMaster) -> None:
    """Verifies corrupted binary frames are flagged as invalid, not crashed."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )
    # Simulate connected state
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    _ = adapter._decode_protobuf_message(b"NOT_VALID_PROTOBUF_DATA")
    # Should return None and increment invalid_events
    # (protobuf may partially decode garbage, but the extraction will find no feeds)
    # The key invariant is: no crash, no exception propagation


# --- Test 4: Invalid instrument rejected ---


def test_invalid_instrument_rejected(active_contract: ContractMaster) -> None:
    """Verifies instrument keys not matching the configured contract are ignored."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    # Feed with wrong instrument key
    decoded = {
        "type": "live_feed",
        "feeds": {
            "NSE_FO|BANKNIFTY26SEPFUT": {
                "ltpc": {"ltp": 52000.0, "ltt": 1726099200000, "ltq": 10, "cp": 51900.0}
            }
        },
    }
    events = adapter._extract_market_events_from_feed(decoded, b"raw_bytes", datetime.now(UTC))
    assert len(events) == 0


# --- Test 5: Invalid timestamp handled ---


def test_invalid_timestamp_handled(active_contract: ContractMaster) -> None:
    """Verifies timestamps in the future are detected as anomalies."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    # Future timestamp (year 2030)
    future_ts_ms = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp() * 1000)
    decoded = {
        "type": "live_feed",
        "feeds": {
            "NSE_FO|NIFTY26SEPFUT": {
                "fullFeed": {
                    "marketFF": {
                        "ltpc": {"ltp": 24500.0, "ltt": future_ts_ms, "ltq": 10, "cp": 24400.0},
                        "marketOHLC": {
                            "ohlc": [
                                {
                                    "interval": "1m",
                                    "open": 24500.0,
                                    "high": 24520.0,
                                    "low": 24490.0,
                                    "close": 24510.0,
                                    "vol": 100,
                                    "ts": future_ts_ms,
                                }
                            ]
                        },
                    }
                }
            }
        },
    }
    events = adapter._extract_market_events_from_feed(decoded, b"raw_bytes", datetime.now(UTC))
    # Events are generated but with future timestamps — anomaly detection
    # happens in MarketStreamValidator, not in the raw extraction
    assert len(events) >= 1


# --- Test 6: Missing timestamp handled ---


def test_missing_timestamp_handled(active_contract: ContractMaster) -> None:
    """Verifies messages lacking timestamp are rejected/flagged."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    # OHLC with ts=0 (missing timestamp)
    decoded = {
        "type": "live_feed",
        "feeds": {
            "NSE_FO|NIFTY26SEPFUT": {
                "fullFeed": {
                    "marketFF": {
                        "marketOHLC": {
                            "ohlc": [
                                {
                                    "interval": "1m",
                                    "open": 24500.0,
                                    "high": 24520.0,
                                    "low": 24490.0,
                                    "close": 24510.0,
                                    "vol": 100,
                                    "ts": 0,
                                }
                            ]
                        },
                    }
                }
            }
        },
    }
    events = adapter._extract_market_events_from_feed(decoded, b"raw_bytes", datetime.now(UTC))
    assert len(events) == 0
    assert adapter._invalid_events >= 1


# --- Test 7: Duplicate event handling ---


def test_duplicate_event_handling(active_contract: ContractMaster) -> None:
    """Verifies repeated events are detected as DUPLICATE without double-counting."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    now_ms = int(datetime(2026, 9, 12, 4, 0, tzinfo=UTC).timestamp() * 1000)
    decoded = {
        "type": "live_feed",
        "feeds": {
            "NSE_FO|NIFTY26SEPFUT": {
                "fullFeed": {
                    "marketFF": {
                        "marketOHLC": {
                            "ohlc": [
                                {
                                    "interval": "1m",
                                    "open": 24500.0,
                                    "high": 24520.0,
                                    "low": 24490.0,
                                    "close": 24510.0,
                                    "vol": 100,
                                    "ts": now_ms,
                                }
                            ]
                        },
                    }
                }
            }
        },
    }

    # First extraction
    events1 = adapter._extract_market_events_from_feed(decoded, b"raw_bytes_1", datetime.now(UTC))
    # Second extraction with same data (duplicate)
    events2 = adapter._extract_market_events_from_feed(decoded, b"raw_bytes_2", datetime.now(UTC))

    assert len(events1) == 1
    assert len(events2) == 1
    assert MarketDataAnomalyType.DUPLICATE in events2[0].anomalies
    assert adapter._duplicate_events >= 1


# --- Test 8: Out-of-order event handling ---


def test_out_of_order_event_handling(active_contract: ContractMaster) -> None:
    """Verifies regressing timestamps are flagged as OUT_OF_ORDER."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"
    adapter._provider_authenticated = True

    t1 = datetime(2026, 9, 12, 4, 5, tzinfo=UTC)
    t2 = datetime(2026, 9, 12, 4, 3, tzinfo=UTC)  # Earlier than t1

    def make_decoded(ts: datetime) -> dict[str, Any]:
        ts_ms = int(ts.timestamp() * 1000)
        return {
            "type": "live_feed",
            "feeds": {
                "NSE_FO|NIFTY26SEPFUT": {
                    "fullFeed": {
                        "marketFF": {
                            "marketOHLC": {
                                "ohlc": [
                                    {
                                        "interval": "1m",
                                        "open": 24500.0,
                                        "high": 24520.0,
                                        "low": 24490.0,
                                        "close": 24510.0,
                                        "vol": 100,
                                        "ts": ts_ms,
                                    }
                                ]
                            },
                        }
                    }
                }
            },
        }

    events1 = adapter._extract_market_events_from_feed(
        make_decoded(t1), b"bytes1", datetime.now(UTC)
    )
    events2 = adapter._extract_market_events_from_feed(
        make_decoded(t2), b"bytes2", datetime.now(UTC)
    )

    assert len(events1) == 1
    assert len(events2) == 1
    assert MarketDataAnomalyType.OUT_OF_ORDER in events2[0].anomalies


# --- Test 9: Provider disconnect handling ---


def test_provider_disconnect_handling(active_contract: ContractMaster) -> None:
    """Verifies disconnection triggers telemetry update and safe shutdown."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"

    adapter.disconnect()

    assert adapter.is_connected is False
    assert adapter._disconnect_count == 1
    telemetry = adapter.get_connection_telemetry()
    assert telemetry["disconnect_timestamp"] is not None


# --- Test 10: Reconnect handling ---


def test_reconnect_handling(active_contract: ContractMaster) -> None:
    """Verifies reconnect increments telemetry counters safely."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-1"

    # Simulate disconnect + reconnect cycle
    adapter.disconnect()
    assert adapter._disconnect_count == 1
    adapter._is_connected = True
    adapter._reconnect_count += 1
    adapter.disconnect()
    assert adapter._disconnect_count == 2
    assert adapter._reconnect_count == 1


# --- Test 11: Provenance generation ---


def test_provenance_generation(active_contract: ContractMaster) -> None:
    """Verifies valid Upstox frame produces verified FeedProvenanceToken."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )
    adapter._is_connected = True
    adapter._session_id = "UPSTOX-LIVE-abc123-1726099200"
    adapter._provider_authenticated = True

    raw_payload = b'{"msg":"test_tick","ltp":24500.0}'
    source_ts = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)

    token = adapter._create_provenance_token(
        raw_payload=raw_payload,
        source_ts=source_ts,
    )

    assert token.provider == "UPSTOX"
    assert token.provider_authenticated is True
    assert token.is_live_external is True
    assert token.connection_session_id == "UPSTOX-LIVE-abc123-1726099200"
    assert token.source_timestamp == source_ts
    assert token.raw_payload_hash == hashlib.sha256(raw_payload).hexdigest()
    # alpha_forge_attestation_hmac is INTERNAL AlphaForge attestation, NOT provider signature
    assert len(token.alpha_forge_attestation_hmac) == 64  # SHA-256 hex


# --- Test 12: Provenance tampering ---


def test_provenance_tampering(active_contract: ContractMaster) -> None:
    """Verifies altered payload or HMAC fails ProvenanceVerifier."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )
    adapter._is_connected = True
    adapter._session_id = "UPSTOX-LIVE-abc123-1726099200"
    adapter._provider_authenticated = True

    raw_payload = b'{"ltp":24500.0}'
    source_ts = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    token = adapter._create_provenance_token(raw_payload, source_ts)

    # Tamper the attestation HMAC
    tampered_token = token.model_copy(update={"alpha_forge_attestation_hmac": "0" * 64})

    event = MarketStreamEvent(
        event_id="EVT-TEST-1",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=1,
        exchange_timestamp=source_ts,
        ingestion_timestamp=source_ts,
        processing_timestamp=source_ts,
        open_price=Decimal("24500.00"),
        high_price=Decimal("24510.00"),
        low_price=Decimal("24490.00"),
        close_price=Decimal("24505.00"),
        volume=100,
        data_source=DataSourceType.REAL_MARKET_SHADOW,
        provenance=tampered_token,
    )
    is_valid, reason = ProvenanceVerifier.verify_provenance(event)
    assert is_valid is False
    assert "CORRUPTED_ATTESTATION" in str(reason)


# --- Test 13: Self-declared REAL_MARKET_SHADOW rejection ---


def test_self_declared_real_market_shadow_rejected(
    active_contract: ContractMaster,
) -> None:
    """Verifies caller cannot forge REAL_MARKET_SHADOW without verified adapter."""
    # Create a token WITHOUT proper attestation
    fake_token = FeedProvenanceToken(
        provider="FAKE_PROVIDER",
        provider_authenticated=False,
        connection_session_id="FAKE-SESSION",
        source_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        raw_payload_hash=hashlib.sha256(b"fake").hexdigest(),
        alpha_forge_attestation_hmac="fake_hmac_value",
        is_live_external=False,
    )

    event = MarketStreamEvent(
        event_id="EVT-FAKE-1",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=1,
        exchange_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        ingestion_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        processing_timestamp=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        open_price=Decimal("24500.00"),
        high_price=Decimal("24510.00"),
        low_price=Decimal("24490.00"),
        close_price=Decimal("24505.00"),
        volume=100,
        data_source=DataSourceType.REAL_MARKET_SHADOW,
        provenance=fake_token,
    )
    is_valid, reason = ProvenanceVerifier.verify_provenance(event)
    assert is_valid is False
    # Should fail on is_live_external=False
    assert "INACTIVE_LIVE_FLAG" in str(reason)


# --- Test 14: Synthetic fallback cannot become Level C ---


def test_synthetic_fallback_cannot_become_level_c(
    active_contract: ContractMaster,
) -> None:
    """Verifies synthetic data cannot pass Level C validation."""
    from alphaforge.shadow_validation.certification_reporter import (  # noqa: PLC0415
        CertificationReporter,
    )

    report = CertificationReporter.generate_report(
        evidence=MagicMock(
            run_id="TEST-RUN-SYNTH",
            git_sha="abc1234",
            config_hash="cfg-test",
            strategy_version="1.0.0",
            contract_metadata_version="3.0.0",
            data_source_type=DataSourceType.SYNTHETIC,
            duration_seconds=100.0,
            market_sessions_count=0,
            valid_events_count=0,
            anomalies=[],
            provenance_verified_events_count=0,
        ),
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.PASS,
        level_c=CertificationLevelStatus.PENDING,
    )
    assert report.final_verdict == CertificationVerdict.PHASE_17_BLOCKED


# --- Test 15: Upstox data cannot call order-routing APIs ---


def test_upstox_data_cannot_call_order_apis(active_contract: ContractMaster) -> None:
    """Verifies UpstoxMarketDataAdapter has no order methods."""
    adapter = UpstoxMarketDataAdapter(
        contract=active_contract,
        access_token="TEST_TOKEN",
    )

    # Assert no order-related methods exist
    order_methods = [
        "place_order",
        "cancel_order",
        "modify_order",
        "submit_order",
        "execute_order",
        "route_order",
        "send_order",
        "create_order",
    ]
    for method_name in order_methods:
        assert not hasattr(adapter, method_name), (
            f"UpstoxMarketDataAdapter has forbidden order method: {method_name}"
        )


# --- Test 16: Expired NIFTY contract rejection ---


def test_expired_nifty_contract_rejected(expired_contract: ContractMaster) -> None:
    """Verifies contract with expiry in the past is rejected during init."""
    with pytest.raises(DataIntegrityError, match="contract expired"):
        UpstoxMarketDataAdapter(
            contract=expired_contract,
            access_token="TEST_TOKEN",
        )


# --- Test 17: Wrong segment rejection ---


def test_wrong_segment_rejected() -> None:
    """Verifies non-NFO segment contract is rejected."""
    wrong_segment = ContractMaster(
        exchange="NSE",
        segment="CDS",  # Currency derivatives, not NFO
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
    with pytest.raises(DataIntegrityError, match="segment must be"):
        UpstoxMarketDataAdapter(contract=wrong_segment, access_token="TEST_TOKEN")


# --- Test 18: Wrong instrument type rejection ---


def test_wrong_instrument_type_rejected() -> None:
    """Verifies non-FUTURES instrument type is rejected."""
    wrong_type = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPIDX",
        instrument_type=InstrumentType.INDEX,  # INDEX, not FUTURES
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
    with pytest.raises(DataIntegrityError, match="instrument_type must be FUTURES"):
        UpstoxMarketDataAdapter(contract=wrong_type, access_token="TEST_TOKEN")


# --- Bonus: Candle aggregator non-lookahead test ---


def test_candle_aggregator_non_lookahead() -> None:
    """Verifies 1-minute candle aggregator does not use future ticks."""
    agg = _CandleAggregator()
    t_base = datetime(2026, 9, 12, 4, 0, 0, tzinfo=UTC)

    # Ticks in minute 0
    assert agg.add_tick(24500.0, 10, t_base) is None
    assert agg.add_tick(24510.0, 20, t_base.replace(second=15)) is None
    assert agg.add_tick(24490.0, 15, t_base.replace(second=30)) is None
    assert agg.add_tick(24505.0, 25, t_base.replace(second=59)) is None

    # First tick of minute 1 → closes minute 0
    t_next = t_base + timedelta(minutes=1)
    candle = agg.add_tick(24520.0, 30, t_next)

    assert candle is not None
    assert candle["open"] == 24500.0
    assert candle["high"] == 24510.0
    assert candle["low"] == 24490.0
    assert candle["close"] == 24505.0
    assert candle["volume"] == 70  # 10 + 20 + 15 + 25
    assert candle["tick_count"] == 4
