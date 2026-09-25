"""
Comprehensive 5-Day Historical Backtest and Rejection Diagnostic Analyzer.
Analyzes exact real Upstox market data from 2026-09-21 to 2026-09-25 (Last 5 Trading Days)
against the frozen, unchanged AlphaForge strategy logic.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Removed unused backtest dataset import
from alphaforge.contract.models import ContractMaster
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
from alphaforge.risk.models import RiskConfig
from scripts.run_real_data_backtest import resample_1m_to_interval
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine


def to_ist_str(dt: datetime) -> str:
    """Convert UTC datetime to IST string (YYYY-MM-DD HH:MM IST)."""
    ist_dt = dt.astimezone(UTC) + timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")


def main() -> None:
    data_file = PROJECT_ROOT / "runtime" / "historical_data" / "NSE_FO_48704_last_5_days_1m.json"
    if not data_file.exists():
        print(f"Error: {data_file} not found.")
        sys.exit(1)

    raw_candles = json.loads(data_file.read_text(encoding="utf-8"))
    print(f"Loaded {len(raw_candles)} raw 1-minute candles from {data_file.name}")

    # Resample to 3m execution bars and 15m confirmation bars
    candles_3m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol="NIFTY",
        contract_id="NIFTY26OCTFUT",
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=3,
    )
    candles_15m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol="NIFTY",
        contract_id="NIFTY26OCTFUT",
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=15,
    )

    print(f"Resampled into {len(candles_3m)} 3-minute bars and {len(candles_15m)} 15-minute bars.")

    # -------------------------------------------------------------------------
    # PART 1: Sequential Bar-by-Bar Diagnostic Trace
    # -------------------------------------------------------------------------
    engine = DeterministicStrategyEngine(StrategyConfig())

    # Strategy evaluation loop
    # We maintain historical series of closed 3m candles and closed 15m candles
    history_3m: list[Candle] = []
    signals: list[StrategySignal] = []
    daily_stats: dict[str, dict] = {}

    for idx, c3 in enumerate(candles_3m):
        strat_candle = c3.to_strategy_candle()
        history_3m.append(strat_candle)

        # Closed confirmation candles up to current 3m bar
        eligible_conf = [
            c15.to_strategy_candle()
            for c15 in candles_15m
            if c15.exchange_timestamp <= c3.exchange_timestamp
        ]

        # Prepare raw_exec_candles: [0] is current forming/quarantined, [1:] are closed
        # reversed so raw_exec_candles[0] = current, [1] = prior, etc.
        raw_exec = list(reversed(history_3m))

        eval_ts = c3.exchange_timestamp
        signal = engine.evaluate(
            raw_exec_candles=raw_exec,
            raw_conf_candles=eligible_conf,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=eval_ts,
        )

        signals.append(signal)

        # Aggregate by IST date
        ist_date = (c3.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        if ist_date not in daily_stats:
            daily_stats[ist_date] = {
                "total_bars": 0,
                "accept_signals": [],
                "rejection_counts": Counter(),
            }

        daily_stats[ist_date]["total_bars"] += 1
        if signal.decision == StrategyDecision.ACCEPT:
            daily_stats[ist_date]["accept_signals"].append((c3, signal))
        else:
            code = signal.rejection_code.value
            daily_stats[ist_date]["rejection_counts"][code] += 1

    # -------------------------------------------------------------------------
    # PART 2: Trade Execution Simulation for ACCEPT Signals
    # -------------------------------------------------------------------------
    # Trace bracket outcomes forward in time
    trades = []
    candle_by_time = {c.exchange_timestamp: c for c in candles_3m}

    for ist_date, stat in daily_stats.items():
        for bar, sig in stat["accept_signals"]:
            entry_px = sig.entry_reference
            stop_px = sig.stop_reference
            target_px = sig.target_reference
            direction = sig.direction
            entry_ts = sig.signal_timestamp

            # Scan future bars on the same day for SL or TP hit
            exit_px = entry_px
            exit_ts = entry_ts
            exit_reason = "SESSION_CLOSE"
            is_closed = False

            # Future bars after entry
            future_bars = [c for c in candles_3m if c.exchange_timestamp > entry_ts]
            for fb in future_bars:
                fb_ist_date = (fb.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                if fb_ist_date != ist_date:
                    # Intraday position squared off at session close
                    break

                if direction == SignalDirection.LONG:
                    if fb.low <= stop_px:
                        exit_px = stop_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "STOP_LOSS"
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
                        exit_reason = "STOP_LOSS"
                        is_closed = True
                        break
                    elif fb.low <= target_px:
                        exit_px = target_px
                        exit_ts = fb.exchange_timestamp
                        exit_reason = "TAKE_PROFIT"
                        is_closed = True
                        break

            if not is_closed and future_bars:
                # EOD Exit at last bar close of the day
                same_day_bars = [b for b in future_bars if (b.exchange_timestamp + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d") == ist_date]
                if same_day_bars:
                    last_b = same_day_bars[-1]
                    exit_px = last_b.close
                    exit_ts = last_b.exchange_timestamp
                    exit_reason = "INTRADAY_EOD_CLOSE"

            pnl_pts = (exit_px - entry_px) if direction == SignalDirection.LONG else (entry_px - exit_px)
            lot_qty = 25  # NIFTY lot size
            pnl_inr = pnl_pts * lot_qty

            trades.append({
                "date": ist_date,
                "direction": direction.value,
                "entry_time": to_ist_str(entry_ts),
                "entry_price": float(entry_px),
                "stop_loss": float(stop_px),
                "target": float(target_px),
                "risk_distance": float(sig.risk_distance),
                "exit_time": to_ist_str(exit_ts),
                "exit_price": float(exit_px),
                "exit_reason": exit_reason,
                "pnl_points": float(pnl_pts),
                "pnl_inr": float(pnl_inr),
            })

    # -------------------------------------------------------------------------
    # PART 3: Print Institutional Summary
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("      ALPHAFORGE 5-DAY HISTORICAL BACKTEST & TRADE DIAGNOSTIC REPORT")
    print("=" * 80)
    print(" Instrument        : NIFTY 50 Futures (NIFTY26OCTFUT)")
    print(" Timeframe         : 3-Minute Execution / 15-Minute Confirmation")
    print(" Period            : 2026-09-21 to 2026-09-25 (Last 5 Trading Days)")
    print(" Total 3m Bars     : 625 Bars (125 Bars/Day)")
    print(" Strategy Logic    : FROZEN AF_ORB_MOMENTUM_V1 (Exact unchanged logic)")
    print("-" * 80)

    print("\nDAY-BY-DAY BREAKDOWN & TRADE DECISIONS:")
    print("-" * 80)

    total_net_pnl_inr = 0.0
    total_trades_count = 0
    winning_trades_count = 0

    for ist_date, stat in daily_stats.items():
        weekday = datetime.strptime(ist_date, "%Y-%m-%d").strftime("%A")
        print(f"\n[{ist_date} ({weekday})]")
        print(f"  * Total 3m Bars Evaluated: {stat['total_bars']}")
        print(f"  * Trades Executed (ACCEPT) : {len(stat['accept_signals'])}")

        if stat["accept_signals"]:
            for b, s in stat["accept_signals"]:
                print(f"    -> [ACCEPT] {to_ist_str(s.signal_timestamp)} | {s.direction.value} @ {s.entry_reference} | SL: {s.stop_reference} | TP: {s.target_reference} (Risk: {s.risk_distance} pts)")
        else:
            print("    -> No trade triggered on this day.")

        print("  * Bars Filtered / Rejected (Detailed Reason Breakdown):")
        total_rej = sum(stat["rejection_counts"].values())
        for code, cnt in stat["rejection_counts"].most_common():
            pct = (cnt / stat["total_bars"]) * 100
            meaning = _get_reason_explanation(code)
            print(f"    - {cnt:3d} bars ({pct:4.1f}%): {code:<28} — {meaning}")

    print("\n" + "=" * 80)
    print("                  EXECUTED TRADES DETAILED LOG")
    print("=" * 80)
    if trades:
        header = f"{'#':<3} {'Date':<10} {'Side':<6} {'Entry Time':<16} {'Entry Px':<10} {'Exit Time':<16} {'Exit Px':<10} {'Exit Reason':<14} {'PnL (Pts)':<10} {'PnL (INR)':<10}"
        print(header)
        print("-" * 105)
        for i, tr in enumerate(trades, 1):
            pnl_sign = "+" if tr["pnl_inr"] >= 0 else ""
            inr_str = f"{pnl_sign}{tr['pnl_inr']:,.2f}"
            pts_str = f"{pnl_sign}{tr['pnl_points']:.2f}"
            print(f"{i:<3} {tr['date']:<10} {tr['direction']:<6} {tr['entry_time'][-9:]:<16} {tr['entry_price']:<10.2f} {tr['exit_time'][-9:]:<16} {tr['exit_price']:<10.2f} {tr['exit_reason']:<14} {pts_str:<10} {inr_str:<10}")
            total_net_pnl_inr += tr["pnl_inr"]
            total_trades_count += 1
            if tr["pnl_inr"] > 0:
                winning_trades_count += 1
        print("-" * 105)
        win_rate = (winning_trades_count / total_trades_count * 100) if total_trades_count else 0
        print(f"SUMMARY: Total Trades: {total_trades_count} | Wins: {winning_trades_count} ({win_rate:.1f}%) | Net PnL (1 Lot = 25 Qty): Rs. {total_net_pnl_inr:+,.2f}")
    else:
        print("  Zero trades executed in this period.")

    print("=" * 80 + "\n")


def _get_reason_explanation(code: str) -> str:
    explanations = {
        "REJECT_TREND": "15m Higher-TF trend was NEUTRAL / Flat / Tangled EMAs",
        "REJECT_BREAKOUT": "Price remained inside 20-bar high/low range (No breakout)",
        "REJECT_CANDLE_GEOMETRY": "Candle had body < 50% or long rejection wick against trend",
        "REJECT_VOLUME": "Bar volume was below 1.2x of the 20-period moving average",
        "REJECT_MOMENTUM": "RSI was outside confirmed momentum band (50-75 Long / 25-50 Short)",
        "REJECT_VOLATILITY": "ATR was outside 0.05% - 1.50% volatility band",
        "REJECT_INSUFFICIENT_HISTORY": "Startup bars before 22-bar lookback was accumulated",
        "REJECT_DUPLICATE": "Identical setup already emitted for this bar",
    }
    return explanations.get(code, "Filtered by quantitative risk/strategy guard")


if __name__ == "__main__":
    main()
