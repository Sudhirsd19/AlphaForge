"""
AlphaForge — Dedicated Backtest & Bar-by-Bar Diagnostic for Today (2026-09-28).
Analyzes exact real Upstox market data from Monday, 2026-09-28.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alphaforge.core.enums import FuturesConfirmationStatus, StrategyDecision, TrendState
from alphaforge.core.models import Candle, StrategySignal
from alphaforge.data.enums import InstrumentType
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from scripts.run_real_data_backtest import resample_1m_to_interval


def main() -> None:
    warmup_file = PROJECT_ROOT / "runtime" / "historical_data" / "NSE_FO_48704_20260923_20260928_1m.json"
    today_file = PROJECT_ROOT / "runtime" / "historical_data" / "NSE_FO_48704_20260928_1m.json"

    warmup_raw = json.loads(warmup_file.read_text(encoding="utf-8")) if warmup_file.exists() else []
    today_raw = json.loads(today_file.read_text(encoding="utf-8")) if today_file.exists() else []

    # Dedup and sort
    dedup: dict[str, list] = {}
    for c in warmup_raw + today_raw:
        dedup[c[0]] = c

    all_raw = sorted(dedup.values(), key=lambda x: x[0])
    print(f"Total unique 1m candles: {len(all_raw)}")

    candles_3m = resample_1m_to_interval(
        all_raw,
        "NIFTY",
        "NIFTY26OCTFUT",
        InstrumentType.FUTURES,
        3,
        filter_regular_session=True,
    )
    candles_15m = resample_1m_to_interval(
        all_raw,
        "NIFTY",
        "NIFTY26OCTFUT",
        InstrumentType.FUTURES,
        15,
        filter_regular_session=True,
    )

    today_date = "2026-09-28"
    prior_3m = [
        c for c in candles_3m
        if (c.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") < today_date
    ]
    today_3m = [
        c for c in candles_3m
        if (c.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") == today_date
    ]

    print(f"Pre-warmup 3m bars: {len(prior_3m)} | Today 3m bars: {len(today_3m)} | Total 15m bars: {len(candles_15m)}")

    strat_config = StrategyConfig(
        strategy_version="1.1.0",
        enable_regime_filter=True,
        min_adx_threshold=Decimal("18.0"),
        min_ema_spread_pct=Decimal("0.0003"),
        enable_entry_cutoff=True,
        entry_cutoff_time_ist="14:30",
    )
    engine = DeterministicStrategyEngine(strat_config)

    history_3m: list[Candle] = [c.to_strategy_candle() for c in prior_3m]
    history_15m: list[Candle] = [c.to_strategy_candle() for c in candles_15m]

    accepts: list[dict] = []
    rejections: dict[str, int] = {}
    hourly: dict[str, dict] = {}
    bar_details: list[dict] = []

    # Calculate day OHLC stats
    day_open = today_3m[0].open
    day_high = max(c.high for c in today_3m)
    day_low = min(c.low for c in today_3m)
    day_close = today_3m[-1].close
    day_volume = sum(c.volume for c in today_3m)

    for idx, c3 in enumerate(today_3m):
        sc3 = c3.to_strategy_candle()
        history_3m.append(sc3)

        eligible_15m = [c for c in history_15m if c.timestamp <= sc3.timestamp]
        raw_exec = [sc3] + list(reversed(history_3m))

        sig = engine.evaluate(
            raw_exec_candles=raw_exec,
            raw_conf_candles=eligible_15m,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=sc3.timestamp,
        )

        ist_ts = sc3.timestamp + timedelta(hours=5, minutes=30)
        time_str = ist_ts.strftime("%H:%M")
        hour_str = ist_ts.strftime("%H:00")

        if hour_str not in hourly:
            hourly[hour_str] = {"total": 0, "accept": 0, "rejections": {}}
        hourly[hour_str]["total"] += 1

        rej_code = sig.rejection_code.value if sig.decision != StrategyDecision.ACCEPT else "ACCEPT"
        if sig.decision == StrategyDecision.ACCEPT:
            hourly[hour_str]["accept"] += 1
            accepts.append({
                "bar_num": idx + 1,
                "time": time_str,
                "direction": sig.direction.value,
                "entry": float(sig.entry_reference),
                "sl": float(sig.stop_reference),
                "tp": float(sig.target_reference),
                "risk_pts": float(sig.risk_distance),
            })
        else:
            rejections[rej_code] = rejections.get(rej_code, 0) + 1
            hourly[hour_str]["rejections"][rej_code] = hourly[hour_str]["rejections"].get(rej_code, 0) + 1

        bar_details.append({
            "bar": idx + 1,
            "time": time_str,
            "open": float(sc3.open),
            "high": float(sc3.high),
            "low": float(sc3.low),
            "close": float(sc3.close),
            "vol": sc3.volume,
            "decision": sig.decision.value,
            "code": rej_code,
            "trend": sig.trend_state.value,
        })

    print("=" * 80)
    print("      ALPHAFORGE TODAY'S REAL MARKET BACKTEST REPORT (2026-09-28)")
    print("=" * 80)
    print(f"Contract        : NIFTY 50 Futures (NIFTY26OCTFUT | NSE_FO|48704)")
    print(f"Session         : Monday, 28 September 2026 (09:15 to 15:30 IST)")
    print(f"Execution TF    : 3-Minute ({len(today_3m)} Bars Evaluated)")
    print(f"Confirmation TF : 15-Minute ({len(history_15m)} Total Bars Available)")
    print("-" * 80)
    print(f"Day Open        : {day_open:,.2f}")
    print(f"Day High        : {day_high:,.2f}")
    print(f"Day Low         : {day_low:,.2f}")
    print(f"Day Close       : {day_close:,.2f}")
    print(f"Day Range (Pts) : {day_high - day_low:.2f} pts ({(day_high - day_low) / day_open * 100:.2f}%)")
    print(f"Net Change      : {day_close - day_open:+.2f} pts ({(day_close - day_open) / day_open * 100:+.2f}%)")
    print(f"Total Volume    : {day_volume:,} contracts")
    print("=" * 80)

    print("\n[EXECUTIVE SUMMARY]")
    print(f"  * Total Trades Executed (ACCEPT) : {len(accepts)}")
    if accepts:
        for a in accepts:
            print(f"    -> [ACCEPT] {a['time']} IST | {a['direction']} @ {a['entry']} | SL: {a['sl']} | TP: {a['tp']} (Risk: {a['risk_pts']} pts)")
    else:
        print("    -> No trade met all 9 strict entry gates today.")

    print("\n[REJECTIONS BREAKDOWN]")
    for k, v in sorted(rejections.items(), key=lambda x: -x[1]):
        pct = (v / len(today_3m)) * 100
        desc = ""
        if k == "REJECT_BREAKOUT":
            desc = "Price remained within the 20-bar High/Low channel (No Breakout)"
        elif k == "REJECT_ENTRY_CUTOFF":
            desc = "Trade barred by 14:30 IST afternoon risk cutoff rule"
        elif k == "REJECT_TREND":
            desc = "15m Higher-TF trend was NEUTRAL / Flat / Tangled EMAs"
        elif k == "REJECT_CANDLE_GEOMETRY":
            desc = "Bar body < 50% or long rejection wick against direction"
        elif k == "REJECT_VOLUME":
            desc = "Bar volume was below 1.20x 20-period moving average"
        elif k == "REJECT_VOLATILITY":
            desc = "ATR volatility outside acceptable 0.05% - 1.50% range"
        print(f"  - {k:<25}: {v:>3} bars ({pct:>5.1f}%) | {desc}")

    print("\n[HOURLY SESSION TRACE]")
    for h, data in sorted(hourly.items()):
        acc = data["accept"]
        rej_str = ", ".join(f"{k}: {v}" for k, v in sorted(data["rejections"].items(), key=lambda x: -x[1]))
        print(f"  {h} IST ({data['total']:>2} bars) -> Accepts: {acc} | {rej_str}")

    print("\n" + "=" * 80)
    print("                    TODAY'S EXECUTED TRADES DETAIL & PnL")
    print("=" * 80)
    if not accepts:
        print("No trades triggered today.")
    else:
        print(f"{'#':<3} {'Time (IST)':<12} {'Side':<6} {'Entry':<10} {'SL':<10} {'TP':<10} {'Exit Time':<12} {'Exit Px':<10} {'Exit Reason':<18} {'PnL (Pts)':<10} {'PnL (INR)':<10}")
        print("-" * 105)
        total_pnl_pts = 0.0
        for i, a in enumerate(accepts):
            entry_t = a["time"]
            entry_px = a["entry"]
            sl_px = a["sl"]
            tp_px = a["tp"]
            direction = a["direction"]
            risk_pts = a["risk_pts"]
            be_moved = False
            exit_time = "15:27"
            exit_px = today_3m[-1].close
            exit_reason = "INTRADAY_EOD_CLOSE"

            # Simulate forward on remaining bars
            for c in today_3m:
                ist_c = c.exchange_timestamp + timedelta(hours=5, minutes=30)
                t_str = ist_c.strftime("%H:%M")
                if t_str <= entry_t:
                    continue
                c_high = float(c.high)
                c_low = float(c.low)

                # Check Breakeven trailing (+1R reached)
                if direction == "SHORT":
                    if (entry_px - c_low) >= risk_pts:
                        be_moved = True
                    if be_moved and c_high >= entry_px:
                        exit_time = t_str
                        exit_px = entry_px
                        exit_reason = "BREAKEVEN_STOP"
                        break
                    if c_high >= sl_px:
                        exit_time = t_str
                        exit_px = sl_px
                        exit_reason = "STOP_LOSS"
                        break
                    if c_low <= tp_px:
                        exit_time = t_str
                        exit_px = tp_px
                        exit_reason = "TAKE_PROFIT"
                        break
                elif direction == "LONG":
                    if (c_high - entry_px) >= risk_pts:
                        be_moved = True
                    if be_moved and c_low <= entry_px:
                        exit_time = t_str
                        exit_px = entry_px
                        exit_reason = "BREAKEVEN_STOP"
                        break
                    if c_low <= sl_px:
                        exit_time = t_str
                        exit_px = sl_px
                        exit_reason = "STOP_LOSS"
                        break
                    if c_high >= tp_px:
                        exit_time = t_str
                        exit_px = tp_px
                        exit_reason = "TAKE_PROFIT"
                        break

            if direction == "SHORT":
                pnl_pts = entry_px - float(exit_px)
            else:
                pnl_pts = float(exit_px) - entry_px
            pnl_inr = pnl_pts * 25.0
            total_pnl_pts += pnl_pts
            print(f"{i+1:<3} {entry_t:<12} {direction:<6} {entry_px:<10.2f} {sl_px:<10.2f} {tp_px:<10.2f} {exit_time:<12} {float(exit_px):<10.2f} {exit_reason:<18} {pnl_pts:<+10.2f} {pnl_inr:<+10.2f}")

        print("-" * 105)
        print(f"TOTAL NET PnL (1 Lot = 25 Qty): {total_pnl_pts:+.2f} Points | Rs. {total_pnl_pts * 25.0:+,.2f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
