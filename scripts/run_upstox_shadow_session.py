#!/usr/bin/env python3
"""
AlphaForge Phase 17C — Upstox Real-Market Shadow Session Runner.

SAFETY: This script consumes live Upstox market data in READ-ONLY shadow mode.
It produces ZERO live orders, ZERO live broker calls.

PROVENANCE: Uses UpstoxMarketDataAdapter which generates authentic
FeedProvenanceToken with alpha_forge_attestation_hmac (INTERNAL AlphaForge
attestation, NOT a provider signature).

CERTIFICATION: This runner does NOT declare PHASE 17 PASS.
It only produces evidence packages for subsequent Phase 17C evaluation.

Usage:
    python scripts/run_upstox_shadow_session.py --mode REAL_MARKET_SHADOW

Environment:
    UPSTOX_ACCESS_TOKEN   — Required. Upstox API access token.
    UPSTOX_INSTRUMENT_KEY — Optional. Defaults to NSE_FO|<contract_id>.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Ensure repository root is on sys.path
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("alphaforge.phase17c.runner")


def get_git_sha() -> str:
    """Get current git SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:7]
    except Exception:
        return "UNKNOWN"


def main() -> None:
    """Run Upstox real-market shadow validation session."""
    parser = argparse.ArgumentParser(
        description="AlphaForge Phase 17C — Upstox Real-Market Shadow Session"
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["REAL_MARKET_SHADOW"],
        help="Execution mode. MUST be REAL_MARKET_SHADOW.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without connecting. Fail-closed test.",
    )
    args = parser.parse_args()

    if args.mode != "REAL_MARKET_SHADOW":
        logger.error("FATAL: Mode must be REAL_MARKET_SHADOW. Got: %s", args.mode)
        sys.exit(1)

    # --- Banner ---
    print("=" * 72)
    print("  ALPHAFORGE PHASE 17C — REAL-MARKET SHADOW VALIDATION")
    print("  MODE: REAL_MARKET_SHADOW")
    print("  LIVE MARKET DATA / ZERO LIVE ORDERS")
    print("  Provider: UPSTOX (READ-ONLY MARKET DATA)")
    print("  Order APIs: NONE (not imported, not accessible)")
    print("=" * 72)

    git_sha = get_git_sha()
    logger.info("Git SHA: %s", git_sha)

    # --- Import AlphaForge components ---
    try:
        from alphaforge.contract.enums import ContractStatus, SettlementType  # noqa: PLC0415
        from alphaforge.contract.models import ContractMaster  # noqa: PLC0415
        from alphaforge.data.enums import InstrumentType  # noqa: PLC0415
        from alphaforge.shadow_validation.upstox_adapter import (  # noqa: PLC0415
            UpstoxMarketDataAdapter,
        )
    except ImportError as exc:
        logger.error("FATAL: Cannot import AlphaForge components — %s", exc)
        sys.exit(1)

    # --- Build active contract ---
    # NOTE: This should be updated to the current active NIFTY Futures contract
    contract = ContractMaster(
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

    logger.info("Contract: %s (expiry: %s)", contract.contract_id, contract.expiry_datetime)

    # --- Create adapter ---
    try:
        adapter = UpstoxMarketDataAdapter(contract=contract)
    except Exception as exc:
        logger.error("FATAL: Adapter creation failed — %s", exc)
        logger.error("PHASE 17C BLOCKED — UPSTOX INTEGRATION INCOMPLETE")
        sys.exit(1)

    logger.info("Adapter created. Provider: %s", adapter.provider_name)

    if args.dry_run:
        logger.info("DRY RUN — checking credentials and configuration only.")
        try:
            adapter.connect()
            logger.info("Connection succeeded (dry run).")
            telemetry = adapter.get_connection_telemetry()
            logger.info("Telemetry: %s", json.dumps(telemetry, indent=2, default=str))

            # Verify critical invariants
            assert adapter.provider_name == "UPSTOX"
            assert adapter.is_connected
            assert telemetry["provider_authenticated"] is True
            assert telemetry["is_live_external"] is True
            logger.info("DRY RUN PASSED — adapter is ready for real-market session.")
            adapter.disconnect()
        except Exception as exc:
            logger.error("DRY RUN FAILED — %s", exc)
            logger.error("PHASE 17C BLOCKED — REAL MARKET DATA UNAVAILABLE")
            sys.exit(1)
        return

    # --- Full session ---
    logger.info("Starting real-market shadow session...")
    try:
        adapter.connect()
    except Exception as exc:
        logger.error("FATAL: Connection failed — %s", exc)
        logger.error("PHASE 17C BLOCKED — REAL MARKET DATA UNAVAILABLE")
        sys.exit(1)

    # Verify live external status before accumulating Level C evidence
    telemetry = adapter.get_connection_telemetry()
    if not telemetry.get("provider_authenticated"):
        logger.error("FATAL: Provider not authenticated. Cannot accumulate Level C evidence.")
        adapter.disconnect()
        sys.exit(1)
    if not telemetry.get("is_live_external"):
        logger.error("FATAL: Not a live external connection. Cannot accumulate Level C evidence.")
        adapter.disconnect()
        sys.exit(1)

    logger.info("LIVE MARKET DATA / ZERO LIVE ORDERS — streaming...")

    event_count = 0
    try:
        for event in adapter.stream_events():
            event_count += 1
            if event_count % 10 == 0:
                logger.info(
                    "Events: %d | Last: %s @ %s | Volume: %d",
                    event_count,
                    event.close_price,
                    event.exchange_timestamp.isoformat(),
                    event.volume,
                )
    except KeyboardInterrupt:
        logger.info("Session interrupted by user.")
    except Exception as exc:
        logger.error("Stream error: %s", exc)

    # --- Final telemetry ---
    final_telemetry = adapter.get_connection_telemetry()
    logger.info("Session complete. Final telemetry:")
    print(json.dumps(final_telemetry, indent=2, default=str))

    # --- Evidence summary ---
    evidence_dir = Path("evidence/shadow_validation")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    now_ts = int(datetime.now(UTC).timestamp())
    evidence_file = evidence_dir / f"upstox_session_{git_sha}_{now_ts}.json"
    with evidence_file.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "run_type": "UPSTOX_REAL_MARKET_SHADOW_SESSION",
                "git_sha": git_sha,
                "provider": "UPSTOX",
                "mode": "REAL_MARKET_SHADOW",
                "live_orders": 0,
                "live_broker_calls": 0,
                "total_events": event_count,
                "telemetry": final_telemetry,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            f,
            indent=2,
            default=str,
        )
    logger.info("Evidence saved: %s", evidence_file)

    if event_count > 0:
        logger.info("PHASE 17C READY FOR REAL-MARKET RUN — %d events captured", event_count)
    else:
        logger.warning(
            "PHASE 17C BLOCKED — No market events received. "
            "Ensure NSE/NFO session is active (09:15–15:30 IST)."
        )


if __name__ == "__main__":
    main()
