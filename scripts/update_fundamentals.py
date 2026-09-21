#!/usr/bin/env python3
# ruff: noqa: E501
"""
AlphaForge Quarterly Fundamentals & SEBI Filings Refresh Utility.
Runs as a standalone maintenance CLI or via system cron (e.g., quarterly/monthly).
Synchronizes company balance sheets, audits filing dates, and calculates quarterly profit growth deltas.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
import urllib.request
import urllib.error

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from alphaforge.fundamentals import COMPILED_UNIVERSE, sync_latest_quarter



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge Quarterly Fundamentals & SEBI Filings Synchronizer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--quarter",
        type=str,
        default="Q1 FY25",
        help="Target quarterly reporting cycle (e.g. 'Q1 FY25', 'Q2 FY25')",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default="http://127.0.0.1:8080",
        help="AlphaForge Dashboard Server base URL for live notification",
    )
    parser.add_argument(
        "--notify-server",
        action="store_true",
        default=True,
        help="Send synchronous notification to running dashboard server if available",
    )
    return parser.parse_args()


def notify_dashboard_server(server_url: str, quarter: str) -> bool:
    """Attempts to trigger server-side sync on the live dashboard process."""
    api_url = f"{server_url.rstrip('/')}/api/fundamentals/sync"
    payload = json.dumps({"quarter": quarter}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                return True
    except Exception:
        return False
    return False


def main() -> int:
    args = parse_args()
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("=" * 76)
    print(" 🚀 ALPHAFORGE QUARTERLY BALANCE SHEET & FUNDAMENTALS REFRESH ENGINE")
    print(f" Timestamp: {now_str}")
    print(f" Target Reporting Cycle: {args.quarter}")
    print("=" * 76)

    # 1. Execute synchronization in engine
    result = sync_latest_quarter(target_quarter=args.quarter)
    updated_stocks = result.get("updated_stocks", [])

    print(f"\n✅ Balance sheet synchronization complete for: {result.get('active_quarter')}")
    print(f"📊 Total Institutional Universe: {result.get('total_universe_count')} companies")
    print(f"⚡ New Quarterly SEBI Filings Detected: {result.get('updated_count')} filings\n")

    # 2. Display detailed audit trail
    print(f"{'#':<3} {'SYMBOL':<12} {'REPORTED':<10} {'FILING DATE':<14} {'QoQ PROFIT':<12} {'QUALITY SCORE'}")
    print("-" * 76)
    for idx, s in enumerate(updated_stocks, start=1):
        profit_val = s.get("profit_growth_qoq", 0.0)
        profit_str = f"+{profit_val:.1f}%" if profit_val >= 0 else f"{profit_val:.1f}%"
        print(f"{idx:<3} {s['symbol']:<12} {s['quarter']:<10} {s['filing_date']:<14} {profit_str:<12} {s['score']}/100")
    print("-" * 76)

    # 3. Notify dashboard server if running
    if args.notify_server:
        server_notified = notify_dashboard_server(args.server_url, args.quarter)
        if server_notified:
            print(f"📡 Dashboard Server ({args.server_url}) successfully notified and refreshed.")
        else:
            print(f"ℹ️ Dashboard Server at {args.server_url} not reachable or running in background.")

    print("\n🏁 All fundamentals metrics & ranking caches successfully updated.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
