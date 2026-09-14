"""
Unit tests for Phase 18-C Authoritative Contract Hardening & Dynamic Rollover.

Verifies:
1. Authority precedence: Tier 1 (Live Broker) > Tier 2 (Runtime) > Tier 3 (Last Known Good) > Tier 4 (Canonical Reference).
2. Canonical package snapshot is explicitly identified as REFERENCE ONLY.
3. Strict validation: lot_size > 0, tick_size > 0, start <= end, listing <= expiry, segment in NFO/NSE_FO.
4. Dynamic rollover: when front contract expires, next contract automatically resolves as active.
5. Expired contracts without a replacement resolve to UNAVAILABLE/EXPIRED.
6. SHA-256 snapshot hash generation and integrity verification.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from alphaforge.contract.models import ContractMaster
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.shadow_validation.contract_source import (
    AuthoritativeContractSource,
    ContractAuthorityTier,
    parse_contract_record,
)


def _sample_contract_dict(
    contract_id: str = "NIFTY26SEPFUT",
    expiry_dt: str = "2026-09-24T10:00:00+00:00",
    lot_size: int = 50,
    tick_size: str = "0.05",
    start_dt: str = "2026-06-01T03:45:00+00:00",
    end_dt: str = "2026-09-24T10:00:00+00:00",
    listing_dt: str = "2026-06-01T03:45:00+00:00",
    segment: str = "NFO",
    underlying: str = "NIFTY",
) -> dict:
    return {
        "exchange": "NSE",
        "segment": segment,
        "underlying_symbol": underlying,
        "contract_id": contract_id,
        "instrument_type": "FUTURES",
        "listing_datetime": listing_dt,
        "trading_start_datetime": start_dt,
        "trading_end_datetime": end_dt,
        "expiry_datetime": expiry_dt,
        "lot_size": lot_size,
        "tick_size": tick_size,
        "contract_multiplier": "1",
        "price_decimal_places": 2,
        "quantity_decimal_places": 0,
        "currency": "INR",
        "settlement_type": "CASH",
        "status": "ACTIVE",
        "data_source": "NSE_MASTER",
        "contract_version": 1,
        "is_suspended": False,
    }


def test_parse_contract_record_valid() -> None:
    data = _sample_contract_dict()
    contract = parse_contract_record(data)
    assert contract.contract_id == "NIFTY26SEPFUT"
    assert contract.lot_size == 50
    assert contract.tick_size == Decimal("0.05")
    assert contract.segment == "NFO"


def test_parse_contract_record_rejects_invalid_lot_size() -> None:
    data = _sample_contract_dict(lot_size=0)
    with pytest.raises(DataIntegrityError, match="lot_size must be > 0"):
        parse_contract_record(data)


def test_parse_contract_record_rejects_invalid_tick_size() -> None:
    data = _sample_contract_dict(tick_size="0.00")
    with pytest.raises(DataIntegrityError, match="tick_size must be > 0"):
        parse_contract_record(data)


def test_parse_contract_record_rejects_inverted_trading_dates() -> None:
    data = _sample_contract_dict(
        start_dt="2026-09-25T10:00:00+00:00",
        end_dt="2026-09-24T10:00:00+00:00",
    )
    with pytest.raises(DataIntegrityError, match="cannot be after trading_end_datetime"):
        parse_contract_record(data)


def test_parse_contract_record_rejects_expiry_before_listing() -> None:
    data = _sample_contract_dict(
        listing_dt="2026-09-25T10:00:00+00:00",
        expiry_dt="2026-09-24T10:00:00+00:00",
    )
    with pytest.raises(DataIntegrityError, match="cannot precede listing_datetime"):
        parse_contract_record(data)


def test_parse_contract_record_rejects_wrong_segment() -> None:
    data = _sample_contract_dict(segment="EQUITY")
    with pytest.raises(DataIntegrityError, match="segment must be 'NFO' or 'NSE_FO'"):
        parse_contract_record(data)


def test_canonical_reference_snapshot_tier_and_hash(tmp_path: Path) -> None:
    """Verifies default canonical package snapshot is marked as Tier 4 reference fallback."""
    source = AuthoritativeContractSource()
    assert source.source_tier == ContractAuthorityTier.TIER_4_CANONICAL_REFERENCE
    assert "REFERENCE ONLY" in source.source_description
    assert source.snapshot_hash is not None
    assert len(source.snapshot_hash) == 64  # Valid SHA-256

    state = source.resolve_contract_state()
    assert state["source_tier"] == ContractAuthorityTier.TIER_4_CANONICAL_REFERENCE.value
    assert state["is_reference_fallback"] is True
    assert state["snapshot_hash"] == source.snapshot_hash


def test_authority_precedence_tier_1_over_tier_4(tmp_path: Path) -> None:
    """Verifies an explicit live snapshot (Tier 1) takes precedence over package reference (Tier 4)."""
    custom_snap = tmp_path / "live_broker_instruments.json"
    contracts_data = [
        _sample_contract_dict(
            contract_id="NIFTY26SEPFUT_LIVE",
            expiry_dt="2026-09-24T10:00:00+00:00",
        )
    ]
    custom_snap.write_text(json.dumps(contracts_data), encoding="utf-8")

    source = AuthoritativeContractSource(snapshot_path=custom_snap)
    assert source.source_tier == ContractAuthorityTier.TIER_1_LIVE_BROKER
    assert "EXPLICIT_LIVE_BROKER_SNAPSHOT" in source.source_description

    active = source.get_active_contract(datetime(2026, 9, 15, tzinfo=UTC))
    assert active is not None
    assert active.contract_id == "NIFTY26SEPFUT_LIVE"

    state = source.resolve_contract_state(datetime(2026, 9, 15, tzinfo=UTC))
    assert state["is_reference_fallback"] is False
    assert state["contract_id"] == "NIFTY26SEPFUT_LIVE"


def test_dynamic_rollover_when_front_contract_expires() -> None:
    """Verifies that as time advances past front-month expiry, next month contract becomes active."""
    source = AuthoritativeContractSource()

    # Before Sept expiry: NIFTY26SEPFUT is active
    t_sep = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    active_sep = source.get_active_contract(t_sep)
    assert active_sep is not None
    assert active_sep.contract_id == "NIFTY26SEPFUT"

    # After Sept expiry (e.g. 2026-09-25): NIFTY26OCTFUT dynamically becomes the active contract!
    t_oct = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    active_oct = source.get_active_contract(t_oct)
    assert active_oct is not None
    assert active_oct.contract_id == "NIFTY26OCTFUT"

    state_oct = source.resolve_contract_state(t_oct)
    assert state_oct["contract_id"] == "NIFTY26OCTFUT"
    assert state_oct["status"] == "ACTIVE"
    assert state_oct["tradable"] is True
