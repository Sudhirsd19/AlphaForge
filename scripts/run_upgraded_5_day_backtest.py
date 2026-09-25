"""
Comprehensive 5-Day Historical Backtest with Upgrades:
1. Zero-lag Historical Warmup (Sep 18 Friday pre-warmed so Monday Sep 21 is fully primed).
2. Trailing Stop Loss to Breakeven at +1R.
3. Afternoon Entry Cutoff Time at 14:30 IST.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.models import Candle, StrategySignal
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from scripts.run_real_data_backtest import resample_1m_to_interval
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine


def to_ist_str(dt: datetime) -> str:
    ist_dt = dt.astimezone(UTC) + timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")


def main() -> None:
    cache_dir = PROJECT_ROOT / "runtime" / "historical_data"
    sep18_file = cache_dir / "NSE_FO_48704_20260918_20260925_1m.json"
    sep21_file = cache_dir / "NSE_FO_48704_last_5_days_1m.json"

    raw_candles = []
    dedup: dict[str, list] = {}

    if sep18_file.exists():
        for c in json.loads(sep18_file.read_text(encoding="utf-8")):
            dedup[c[0]] = c

    if sep21_file.exists():
        for c in json.loads(sep21_file.read_text(encoding="utf-8")):
            dedup[c[0]] = c

    all_raw = sorted(dedup.values(), key=lambda c: c[0])
    print(f"Loaded {len(all_raw)} total 1-minute historical candles (including pre-warmup).")

    # Resample
    candles_3m = resample_1m_to_interval(
        raw_candles=all_raw,
        symbol="NIFTY",
        contract_id="NIFTY26OCTFUT",
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=3,
    )
    candles_15m = resample_1m_to_interval(
        raw_candles=all_raw,
        symbol="NIFTY",
        contract_id="NIFTY26OCTFUT",
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=15,
    )

    print(f"Resampled: {len(candles_3m)} 3m bars, {len(candles_15m)} 15m bars.")

    # Upgraded Strategy Configuration:
    # 1. enable_entry_cutoff = True (14:30 IST)
    cfg = StrategyConfig(
        strategy_version="1.1.0-UPGRADED",
        enable_entry_cutoff=True,
        entry_cutoff_time_ist="14:30",
    )
    engine = DeterministicStrategyEngine(cfg)

    # Filter target 5 days (Sep 21 to Sep 25, 2026)
    target_dates = {"2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"}

    # Primed history before Monday Sep 21 09:15
    history_3m: list[Candle] = [
        c.to_strategy_candle()
        for c in candles_3m
        if (c.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") < "2026-09-21"
    ]
    print(f"Warmup pre-loaded {len(history_3m)} 3m bars from prior trading session.")

    bars_5d = [
        c for c in candles_3m
        if (c.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") in target_dates
    ]
    print(f"Evaluating {len(bars_5d)} bars across the 5 target trading days.")

    daily_stats: dict[str, dict] = {}
    all_signals: list[tuple[MarketCandle, StrategySignal]] = []

    for c3 in bars_5d:
        strat_candle = c3.to_strategy_candle()
        history_3m.append(strat_candle)

        # 15m confirmation history up to current 3m bar
        eligible_conf = [
            c15.to_strategy_candle()
            for c15 in candles_15m
            if c15.exchange_timestamp <= c3.exchange_timestamp
        ]

        # Trigger bar is at index [1], index [0] is quarantined
        raw_exec = [strat_candle] + list(reversed(history_3m[:-1]))

        eval_ts = c3.exchange_timestamp
        sig = engine.evaluate(
            raw_exec_candles=raw_exec,
            raw_conf_candles=eligible_conf,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=eval_ts,
        )

        all_signals.append((c3, sig))

        ist_date = (c3.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        if ist_date not in daily_stats:
            daily_stats[ist_date] = {
                "total_bars": 0,
                "accept_signals": [],
                "rejection_counts": Counter(),
            }

        daily_stats[ist_date]["total_bars"] += 1
        if sig.decision == StrategyDecision.ACCEPT:
            daily_stats[ist_date]["accept_signals"].append((c3, sig))
        else:
            daily_stats[ist_date]["rejection_counts"][sig.rejection_code.value] += 1

    # -------------------------------------------------------------------------
    # PART 2: Trade Execution with Trailing Stop to Breakeven at +1R
    # -------------------------------------------------------------------------
    trades = []
    
    for ist_date, stat in daily_stats.items():
        for bar, sig in stat["accept_signals"]:
            entry_px = sig.entry_reference
            initial_stop_px = sig.stop_reference
            stop_px = initial_stop_px
            target_px = sig.target_reference
            direction = sig.direction
            entry_ts = sig.signal_timestamp
            risk_dist = sig.risk_distance

            exit_px = entry_px
            exit_ts = entry_ts
            exit_reason = "SESSION_CLOSE"
            is_breakeven_trailed = False
            is_closed = False

            # Future bars on the same day after entry
            future_bars = [c for c in candles_3m if c.exchange_timestamp > entry_ts]
            for fb in future_bars:
                fb_ist_date = (fb.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                if fb_ist_date != ist_date:
                    break

                # 1. Check Trailing Stop to Breakeven at +1R
                if not is_breakeven_trailed:
                    if direction == SignalDirection.LONG:
                        if fb.high >= (entry_px + risk_dist):
                            stop_px = entry_px
                            is_breakeven_trailed = True
                    elif direction == SignalDirection.SHORT:
                        if fb.low <= (entry_px - risk_dist):
                            stop_px = entry_px
                            is_breakeven_trailed = True

                # 2. Check Stop Loss / Target Hit
                if direction == SignalDirection.LONG:
                    if fb.low <= stop_px:
                        exit_px = stop_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "BREAKEVEN_STOP" if is_breakeven_trailed else "STOP_LOSS"
                        is_closed = True
                        break
                    elif fb.high >= target_px:
                        exit_px = target_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "TAKE_PROFIT"
                        is_closed = True
                        break
                elif direction == SignalDirection.SHORT:
                    if fb.high >= stop_px:
                        exit_px = stop_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "BREAKEVEN_STOP" if is_breakeven_trailed else "STOP_LOSS"
                        is_closed = True
                        break
                    elif fb.low <= target_px:
                        exit_px = target_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "TAKE_PROFIT"
                        is_closed = True
                        break

            if not is_closed and future_bars:
                same_day_bars = [
                    b for b in future_bars
                    if (b.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") == ist_date
                ]
                if same_day_bars:
                    last_b = same_day_bars[-1]
                    exit_px = last_b.close
                    exit_ts = last_b.exchange_timestamp
                    exit_reason = "INTRADAY_EOD_CLOSE"

            pnl_pts = (exit_px - entry_px) if direction == SignalDirection.LONG else (entry_px - exit_px)
            lot_qty = 25
            pnl_inr = pnl_pts * lot_qty

            trades.append({
                "date": ist_date,
                "direction": direction.value,
                "entry_time": to_ist_str(entry_ts),
                "entry_price": float(entry_px),
                "initial_stop": float(initial_stop_px),
                "exit_time": to_ist_str(exit_ts),
                "exit_price": float(exit_px),
                "exit_reason": exit_reason,
                "is_trailed": is_breakeven_trailed,
                "pnl_points": float(pnl_pts),
                "pnl_inr": float(pnl_inr),
            })

    # -------------------------------------------------------------------------
    # PART 3: Print Institutional Report
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("      ALPHAFORGE 5-DAY UPGRADED BACKTEST & DIAGNOSTIC REPORT")
    print("=" * 80)
    print(" Instrument        : NIFTY 50 Futures (NIFTY26OCTFUT)")
    print(" Timeframe         : 3-Minute Execution / 15-Minute Confirmation")
    print(" Period            : 2026-09-21 to 2026-09-25 (5 Trading Days)")
    print(" Total 3m Bars     : 625 Bars (125 Bars/Day)")
    print(" Upgrades Active   : 1. Startup Warmup | 2. Breakeven at +1R | 3. 14:30 Entry Cutoff")
    print("-" * 80)

    print("\nDAY-BY-DAY BREAKDOWN & REASONS:")
    print("-" * 80)
    for ist_date in sorted(daily_stats.keys()):
        stat = daily_stats[ist_date]
        accepts = stat["accept_signals"]
        day_trades = [t for t in trades if t["date"] == ist_date]
        day_pnl_inr = sum(t["pnl_inr"] for t in day_trades)
        day_pts = sum(t["pnl_points"] for t in day_trades)

        print(f"\n[{ist_date}]")
        print(f"  * Total 3m Bars Evaluated: {stat['total_bars']}")
        print(f"  * Trades Executed (ACCEPT) : {len(accepts)} | Day PnL: Rs. {day_pnl_inr:+,.2f} ({day_pts:+.2f} pts)")
        
        if accepts:
            for b, sig in accepts:
                t_str = to_ist_str(sig.signal_timestamp)
                print(f"    -> [ACCEPT] {t_str} | {sig.direction.value} @ {sig.entry_reference} | SL: {sig.stop_reference:.2f} | TP: {sig.target_reference:.2f}")
        else:
            print("    -> No trade triggered on this day.")

        print("  * Rejection Breakdown (Why other bars didn't execute):")
        total_rej = sum(stat["rejection_counts"].values())
        for code, cnt in stat["rejection_counts"].most_common():
            pct = (cnt / stat["total_bars"]) * 100
            print(f"    - {cnt:3d} bars ({pct:4.1f}%): {code}")

    print("\n" + "=" * 90)
    print("                         UPGRADED EXECUTED TRADES LOG")
    print("=" * 90)
    print(f"{'#':<3} {'Date':<10} {'Side':<6} {'Entry Time':<16} {'Entry Px':<10} {'Exit Time':<16} {'Exit Px':<10} {'Exit Reason':<16} {'PnL Pts':<10} {'PnL (INR)':<10}")
    print("-" * 90)
    total_inr = 0.0
    total_pts = 0.0
    wins = 0
    breakevens = 0
    losses = 0

    for idx, t in enumerate(trades, 1):
        p_inr = t["pnl_inr"]
        p_pts = t["pnl_points"]
        total_inr += p_inr
        total_pts += p_pts
        if p_pts > 0:
            wins += 1
        elif p_pts == 0 or t["exit_reason"] == "BREAKEVEN_STOP":
            breakevens += 1
        else:
            losses += 1

        print(f"{idx:<3} {t['date']:<10} {t['direction']:<6} {t['entry_time'][11:]:<16} {t['entry_price']:<10.2f} {t['exit_time'][11:]:<16} {t['exit_price']:<10.2f} {t['exit_reason']:<16} {p_pts:<+10.2f} Rs. {p_inr:<+9.2f}")

    print("-" * 90)
    total_cnt = len(trades)
    win_rate = (wins / total_cnt * 100) if total_cnt else 0.0
    print(f"SUMMARY: Total Trades: {total_cnt} | Wins: {wins} | Breakevens: {breakevens} | Losses: {losses} | Win Rate: {win_rate:.1f}%")
    print(f"TOTAL NET PNL (1 Lot = 25 Qty): Rs. {total_inr:+,.2f} ({total_pts:+.2f} points)")
    print("=" * 90)


if __name__ == "__main__":
    main()
