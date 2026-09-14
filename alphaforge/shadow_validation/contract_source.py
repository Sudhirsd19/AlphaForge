# ruff: noqa: E402
"""
Authoritative Contract Metadata Source for AlphaForge Shadow Validation and Console (Phase 17).
Consumes authoritative exchange/broker contract definitions, enforces fail-closed
lifecycle verification, automatic contract rollover, and strict separation between
authoritative real-market contracts and synthetic/test contracts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.lifecycle import (
    evaluate_contract_lifecycle,
    get_current_active_contract,
    get_next_contract,
    get_rollover_candidate,
    is_tradeable,
)
from alphaforge.contract.models import ContractMaster
from alphaforge.contract.repository import InMemoryContractMasterRepository
from alphaforge.contract.validation import validate_contract_master
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType

logger = logging.getLogger(__name__)

ENV_CONTRACT_SNAPSHOT = "ALPHAFORGE_CONTRACT_SNAPSHOT_PATH"
ENV_UPSTOX_INSTRUMENTS = "UPSTOX_INSTRUMENTS_PATH"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_BROKER_SNAPSHOT = REPO_ROOT / "runtime" / "contracts" / "live_broker_instruments.json"
RUNTIME_SNAPSHOT = REPO_ROOT / "runtime" / "contracts" / "nifty_contracts.json"
LAST_KNOWN_GOOD_SNAPSHOT = REPO_ROOT / "runtime" / "contracts" / "last_known_good_contracts.json"
PACKAGE_SNAPSHOT = Path(__file__).resolve().parent / "canonical_contracts.json"


class ContractAuthorityTier(StrEnum):
    """Authoritative hierarchy tiers for contract metadata."""

    TIER_1_LIVE_BROKER = "TIER_1_LIVE_BROKER_MASTER"
    TIER_2_VERIFIED_RUNTIME = "TIER_2_VERIFIED_RUNTIME_SNAPSHOT"
    TIER_3_LAST_KNOWN_GOOD = "TIER_3_LAST_KNOWN_GOOD"
    TIER_4_CANONICAL_REFERENCE = "TIER_4_CANONICAL_REFERENCE_ONLY"
    UNAVAILABLE = "UNAVAILABLE"



def parse_contract_record(data: dict[str, Any]) -> ContractMaster:
    """Parse raw dictionary into a strictly validated ContractMaster instance."""
    raw_expiry = data["expiry_datetime"]
    expiry_dt = (
        raw_expiry if isinstance(raw_expiry, datetime) else datetime.fromisoformat(str(raw_expiry))
    )
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=UTC)
    else:
        expiry_dt = expiry_dt.astimezone(UTC)

    raw_listing = data["listing_datetime"]
    listing_dt = (
        raw_listing
        if isinstance(raw_listing, datetime)
        else datetime.fromisoformat(str(raw_listing))
    )
    listing_dt = (
        listing_dt.replace(tzinfo=UTC) if listing_dt.tzinfo is None else listing_dt.astimezone(UTC)
    )

    raw_start = data["trading_start_datetime"]
    start_dt = (
        raw_start if isinstance(raw_start, datetime) else datetime.fromisoformat(str(raw_start))
    )
    start_dt = start_dt.replace(tzinfo=UTC) if start_dt.tzinfo is None else start_dt.astimezone(UTC)

    raw_end = data["trading_end_datetime"]
    end_dt = raw_end if isinstance(raw_end, datetime) else datetime.fromisoformat(str(raw_end))
    end_dt = end_dt.replace(tzinfo=UTC) if end_dt.tzinfo is None else end_dt.astimezone(UTC)

    raw_status = data.get("status", "ACTIVE")
    status = (
        raw_status if isinstance(raw_status, ContractStatus) else ContractStatus(str(raw_status))
    )

    raw_inst_type = data.get("instrument_type", "FUTURES")
    inst_type = (
        raw_inst_type
        if isinstance(raw_inst_type, InstrumentType)
        else InstrumentType(str(raw_inst_type))
    )

    raw_settlement = data.get("settlement_type", "CASH")
    settlement = (
        raw_settlement
        if isinstance(raw_settlement, SettlementType)
        else SettlementType(str(raw_settlement))
    )

    lot_size = int(data["lot_size"])
    if lot_size <= 0:
        raise DataIntegrityError(f"lot_size must be > 0, got {lot_size}")

    tick_size = Decimal(str(data["tick_size"]))
    if tick_size <= Decimal("0"):
        raise DataIntegrityError(f"tick_size must be > 0, got {tick_size}")

    if start_dt > end_dt:
        raise DataIntegrityError(
            f"trading_start_datetime ({start_dt}) cannot be after trading_end_datetime ({end_dt})"
        )

    if expiry_dt < listing_dt:
        raise DataIntegrityError(
            f"expiry_datetime ({expiry_dt}) cannot precede listing_datetime ({listing_dt})"
        )

    underlying = str(data["underlying_symbol"]).strip().upper()
    if underlying != "NIFTY":
        raise DataIntegrityError(f"underlying_symbol must be 'NIFTY', got '{underlying}'")

    segment = str(data["segment"]).strip().upper()
    if segment not in ("NFO", "NSE_FO"):
        raise DataIntegrityError(f"segment must be 'NFO' or 'NSE_FO', got '{segment}'")

    contract = ContractMaster(
        exchange=str(data["exchange"]).strip().upper(),
        segment=segment,
        underlying_symbol=underlying,
        contract_id=str(data["contract_id"]).strip().upper(),
        instrument_type=inst_type,
        listing_datetime=listing_dt,
        trading_start_datetime=start_dt,
        trading_end_datetime=end_dt,
        expiry_datetime=expiry_dt,
        lot_size=lot_size,
        tick_size=tick_size,
        contract_multiplier=Decimal(str(data.get("contract_multiplier", "1"))),
        price_decimal_places=int(data.get("price_decimal_places", 2)),
        quantity_decimal_places=int(data.get("quantity_decimal_places", 0)),
        currency=str(data.get("currency", "INR")).strip().upper(),
        settlement_type=settlement,
        status=status,
        data_source=str(data.get("data_source", "AUTHORITATIVE_SNAPSHOT")),
        contract_version=int(data.get("contract_version", 1)),
        is_suspended=bool(data.get("is_suspended", False)),
    )
    validate_contract_master(contract)
    return contract


class AuthoritativeContractSource:
    """
    Authoritative Contract Metadata Source for AlphaForge.

    Enforces strict priority hierarchy:
    1. External snapshot (ALPHAFORGE_CONTRACT_SNAPSHOT_PATH, UPSTOX_INSTRUMENTS_PATH)
    2. Authoritative Contract Master repository
    3. Safely persisted validated contract snapshot (runtime or canonical_contracts.json)
    4. Fail-closed UNAVAILABLE state (never silently uses stale or expired objects)

    Guarantees:
    - Zero synthetic contracts presented as real-market truth.
    - Deterministic contract rollover when front-month contract expires.
    - 100% contract identity alignment between Dashboard and UpstoxMarketDataAdapter.
    """

    def __init__(
        self,
        repository: InMemoryContractMasterRepository | None = None,
        snapshot_path: Path | None = None,
        allow_synthetic: bool = False,
    ) -> None:
        self._allow_synthetic = allow_synthetic
        self._repository = repository or InMemoryContractMasterRepository()
        self._snapshot_path = snapshot_path
        self._source_tier = ContractAuthorityTier.UNAVAILABLE
        self._source_description = "UNINITIALIZED"
        self._snapshot_hash: str | None = None
        self._snapshot_mtime: datetime | None = None
        self.load_contracts()

    @property
    def repository(self) -> InMemoryContractMasterRepository:
        """Return underlying contract repository."""
        return self._repository

    @property
    def source_tier(self) -> ContractAuthorityTier:
        """Return the authoritative tier of the active contract metadata."""
        return self._source_tier

    @property
    def source_description(self) -> str:
        """Return description of active contract metadata source."""
        return self._source_description

    @property
    def snapshot_hash(self) -> str | None:
        """SHA-256 digest of loaded contract snapshot file."""
        return self._snapshot_hash

    @property
    def snapshot_mtime(self) -> datetime | None:
        """Filesystem modification timestamp of loaded contract snapshot."""
        return self._snapshot_mtime

    def load_contracts(self) -> int:
        """
        Load contract metadata into repository adhering to strict 4-tier priority hierarchy:
        Tier 1: Live Broker / Exchange Instrument Master
        Tier 2: Verified Runtime Snapshot
        Tier 3: Last-Known-Good Persisted Snapshot
        Tier 4: Canonical Package Snapshot (REFERENCE ONLY — NOT LIVE EXCHANGE TRUTH)
        """
        # Tier 1: Explicitly provided snapshot path or live broker path
        if self._snapshot_path and self._snapshot_path.exists():
            loaded = self._load_file(
                self._snapshot_path,
                ContractAuthorityTier.TIER_1_LIVE_BROKER,
                "EXPLICIT_LIVE_BROKER_SNAPSHOT",
            )
            if loaded > 0:
                return loaded

        env_upstox = os.environ.get(ENV_UPSTOX_INSTRUMENTS)
        if env_upstox and Path(env_upstox).exists():
            loaded = self._load_file(
                Path(env_upstox),
                ContractAuthorityTier.TIER_1_LIVE_BROKER,
                "UPSTOX_INSTRUMENTS_MASTER",
            )
            if loaded > 0:
                return loaded

        # Tier 2: Verified runtime directory persisted snapshot
        if RUNTIME_SNAPSHOT.exists():
            loaded = self._load_file(
                RUNTIME_SNAPSHOT,
                ContractAuthorityTier.TIER_2_VERIFIED_RUNTIME,
                "RUNTIME_VERIFIED_SNAPSHOT",
            )
            if loaded > 0:
                return loaded

        # Tier 3: Last-known-good persisted snapshot
        if LAST_KNOWN_GOOD_SNAPSHOT.exists():
            loaded = self._load_file(
                LAST_KNOWN_GOOD_SNAPSHOT,
                ContractAuthorityTier.TIER_3_LAST_KNOWN_GOOD,
                "LAST_KNOWN_GOOD_SNAPSHOT",
            )
            if loaded > 0:
                return loaded

        # Prior repository if explicitly prepopulated
        if self._repository.count() > 0:
            self._source_tier = ContractAuthorityTier.TIER_2_VERIFIED_RUNTIME
            self._source_description = "IN_MEMORY_PREPOPULATED_REPOSITORY"
            return self._repository.count()

        # Tier 4: Canonical package reference snapshot (FALLBACK REFERENCE ONLY)
        if PACKAGE_SNAPSHOT.exists():
            loaded = self._load_file(
                PACKAGE_SNAPSHOT,
                ContractAuthorityTier.TIER_4_CANONICAL_REFERENCE,
                "PACKAGE_CANONICAL_SNAPSHOT (REFERENCE ONLY — NOT LIVE EXCHANGE TRUTH)",
            )
            if loaded > 0:
                return loaded

        self._source_tier = ContractAuthorityTier.UNAVAILABLE
        self._source_description = "UNAVAILABLE"
        logger.warning(
            "AuthoritativeContractSource: No contract metadata source available. Failing closed."
        )
        return 0

    def _load_file(self, path: Path, tier: ContractAuthorityTier, source_label: str) -> int:
        try:
            raw_bytes = path.read_bytes()
            content = raw_bytes.decode("utf-8")
            self._snapshot_hash = hashlib.sha256(raw_bytes).hexdigest()
            self._snapshot_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            raw_list = json.loads(content)
            if not isinstance(raw_list, list):
                logger.error(
                    "AuthoritativeContractSource: Expected JSON list in %s, got %s",
                    path,
                    type(raw_list),
                )
                return 0

            count = 0
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                try:
                    c = parse_contract_record(item)
                    if not self._allow_synthetic and c.data_source.upper().startswith(
                        ("SYNTHETIC", "TEST")
                    ):
                        logger.warning(
                            "AuthoritativeContractSource: Rejecting synthetic contract '%s'",
                            c.contract_id,
                        )
                        continue
                    self._repository.add_contract(c)
                    count += 1
                except Exception as exc:
                    logger.error(
                        f"AuthoritativeContractSource: Invalid contract record in {path}: {exc}"
                    )

            if count > 0:
                self._source_tier = tier
                self._source_description = f"{source_label}:{path.name}"
                logger.info(
                    f"AuthoritativeContractSource [{tier.value}]: "
                    f"Loaded {count} contracts from {path}"
                )
            return count
        except Exception as exc:
            logger.error(f"AuthoritativeContractSource: Failed to read {path}: {exc}")
            return 0

    def get_contracts(self, underlying_symbol: str = "NIFTY") -> list[ContractMaster]:
        """
        Retrieve all known valid contracts for underlying in deterministic expiry order.
        Filters out synthetic/test contracts if not explicitly permitted.
        """
        all_c = self._repository.get_contracts_for_underlying(underlying_symbol)
        if self._allow_synthetic:
            return all_c
        return [c for c in all_c if not c.data_source.upper().startswith(("SYNTHETIC", "TEST"))]

    def get_active_contract(
        self,
        evaluation_timestamp: datetime | None = None,
        underlying_symbol: str = "NIFTY",
    ) -> ContractMaster | None:
        """
        Retrieve the current authoritative active contract (front-month).
        Fails closed (returns None) if:
        - No contracts are registered
        - All contracts are expired relative to evaluation_timestamp
        - Contract is explicitly declared INVALID or SUSPENDED
        """
        eval_ts = evaluation_timestamp or datetime.now(UTC)
        contracts = self.get_contracts(underlying_symbol)
        if not contracts:
            return None

        current = get_current_active_contract(contracts, eval_ts, include_expiring=True)
        if current is None:
            return None

        # Double check tradability
        if not is_tradeable(current, eval_ts, allow_expiring=True):
            return None

        return current

    def get_next_contract(
        self,
        evaluation_timestamp: datetime | None = None,
        underlying_symbol: str = "NIFTY",
    ) -> ContractMaster | None:
        """Retrieve next-month active contract or None."""
        eval_ts = evaluation_timestamp or datetime.now(UTC)
        contracts = self.get_contracts(underlying_symbol)
        return get_next_contract(contracts, eval_ts)

    def get_rollover_candidate(
        self,
        evaluation_timestamp: datetime | None = None,
        underlying_symbol: str = "NIFTY",
    ) -> tuple[ContractMaster, ContractMaster] | None:
        """
        Return (front_expiring, next_active) rollover pair if front contract
        is in its EXPIRING window, otherwise None.
        """
        eval_ts = evaluation_timestamp or datetime.now(UTC)
        contracts = self.get_contracts(underlying_symbol)
        return get_rollover_candidate(contracts, eval_ts)

    def resolve_contract_state(
        self,
        evaluation_timestamp: datetime | None = None,
        underlying_symbol: str = "NIFTY",
    ) -> dict[str, Any]:
        """
        Produce complete, honest contract state dictionary for UI and status endpoints.
        Accurately reflects ACTIVE, EXPIRING, EXPIRED, UNAVAILABLE, and INVALID states.
        """
        eval_ts = evaluation_timestamp or datetime.now(UTC)

        active = self.get_active_contract(eval_ts, underlying_symbol)
        rollover_pair = self.get_rollover_candidate(eval_ts, underlying_symbol)
        next_c = self.get_next_contract(eval_ts, underlying_symbol)

        if active is not None:
            lifecycle = evaluate_contract_lifecycle(active, eval_ts)
            tradable = is_tradeable(active, eval_ts, allow_expiring=True)
            is_synth = active.data_source.upper().startswith(("SYNTHETIC", "TEST"))

            rollover_data: dict[str, Any] | None = None
            if rollover_pair:
                rollover_data = {
                    "status": "ROLLOVER_PENDING",
                    "front_contract": rollover_pair[0].contract_id,
                    "next_contract": rollover_pair[1].contract_id,
                    "expiry_hours_remaining": round(
                        (rollover_pair[0].expiry_datetime - eval_ts).total_seconds() / 3600.0,
                        2,
                    ),
                }

            return {
                "status": lifecycle.value,
                "lifecycle": lifecycle.value,
                "lifecycle_status": lifecycle.value,
                "tradable": tradable,
                "contract_id": active.contract_id,
                "underlying": active.underlying_symbol,
                "underlying_symbol": active.underlying_symbol,
                "exchange": active.exchange,
                "segment": active.segment,
                "instrument_type": active.instrument_type.value,
                "expiry": active.expiry_datetime.isoformat(),
                "expiry_datetime": active.expiry_datetime.isoformat(),
                "lot_size": active.lot_size,
                "tick_size": str(active.tick_size),
                "multiplier": str(active.contract_multiplier),
                "currency": active.currency,
                "data_source": active.data_source,
                "is_synthetic": is_synth,
                "rollover": rollover_data,
                "next_contract": (
                    {
                        "contract_id": next_c.contract_id,
                        "expiry": next_c.expiry_datetime.isoformat(),
                    }
                    if next_c
                    else None
                ),
                "source_tier": self._source_tier.value,
                "source_description": self._source_description,
                "snapshot_hash": self._snapshot_hash,
                "snapshot_freshness_seconds": (
                    round((eval_ts - self._snapshot_mtime).total_seconds(), 1)
                    if self._snapshot_mtime
                    else None
                ),
                "is_reference_fallback": (
                    self._source_tier == ContractAuthorityTier.TIER_4_CANONICAL_REFERENCE
                ),
            }

        # If no active contract, check if there are known contracts that are expired
        all_underlying = self._repository.get_contracts_for_underlying(underlying_symbol)
        if all_underlying:
            latest_c = all_underlying[-1]
            st = evaluate_contract_lifecycle(latest_c, eval_ts)
            return {
                "status": st.value if st == ContractStatus.EXPIRED else "UNAVAILABLE",
                "lifecycle": st.value if st == ContractStatus.EXPIRED else "UNAVAILABLE",
                "lifecycle_status": st.value if st == ContractStatus.EXPIRED else "UNAVAILABLE",
                "tradable": False,
                "contract_id": latest_c.contract_id if st == ContractStatus.EXPIRED else "NONE",
                "underlying": underlying_symbol,
                "underlying_symbol": underlying_symbol,
                "exchange": "NSE",
                "segment": "NFO",
                "instrument_type": "FUTURES",
                "expiry": latest_c.expiry_datetime.isoformat()
                if st == ContractStatus.EXPIRED
                else None,
                "expiry_datetime": latest_c.expiry_datetime.isoformat()
                if st == ContractStatus.EXPIRED
                else None,
                "lot_size": latest_c.lot_size if st == ContractStatus.EXPIRED else 0,
                "tick_size": str(latest_c.tick_size) if st == ContractStatus.EXPIRED else "0.00",
                "multiplier": "0",
                "currency": "INR",
                "data_source": "UNAVAILABLE",
                "is_synthetic": False,
                "rollover": None,
                "next_contract": None,
                "source_tier": self._source_tier.value,
                "source_description": self._source_description,
                "snapshot_hash": self._snapshot_hash,
                "snapshot_freshness_seconds": (
                    round((eval_ts - self._snapshot_mtime).total_seconds(), 1)
                    if self._snapshot_mtime
                    else None
                ),
                "is_reference_fallback": (
                    self._source_tier == ContractAuthorityTier.TIER_4_CANONICAL_REFERENCE
                ),
                "error": (
                    f"Contract {latest_c.contract_id} is EXPIRED. "
                    "No subsequent active contract registered."
                ),
            }

        # Completely empty repository / unavailable
        return {
            "status": "UNAVAILABLE",
            "lifecycle": "UNAVAILABLE",
            "lifecycle_status": "UNAVAILABLE",
            "tradable": False,
            "contract_id": "NONE",
            "underlying": underlying_symbol,
            "underlying_symbol": underlying_symbol,
            "exchange": "NSE",
            "segment": "NFO",
            "instrument_type": "FUTURES",
            "expiry": None,
            "expiry_datetime": None,
            "lot_size": 0,
            "tick_size": "0.00",
            "multiplier": "0",
            "currency": "INR",
            "data_source": "UNAVAILABLE",
            "is_synthetic": False,
            "rollover": None,
            "next_contract": None,
            "source_tier": self._source_tier.value,
            "source_description": "UNAVAILABLE",
            "snapshot_hash": None,
            "snapshot_freshness_seconds": None,
            "is_reference_fallback": False,
            "error": (
                "No contract metadata available. CONTRACT STATUS = UNAVAILABLE, TRADABLE = FALSE"
            ),
        }
