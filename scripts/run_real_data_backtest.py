#!/usr/bin/env python3
# ruff: noqa: E402, E501
"""
AlphaForge — Real Market Data Backtest Runner.

Executes deterministic historical backtesting against real historical market data
fetched from Upstox API v2 (NSE NIFTY Futures / Index).

STRICT INVARIANT:
Zero modifications to strategy algorithms, risk gates, or execution state machines.
All simulation passes through authoritative Phase 1-9 BacktestEngine.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

# Ensure repo root is on sys.path
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import BacktestConfig, FinalPositionPolicy
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.models import RiskConfig
from alphaforge.shadow_validation.contract_source import get_current_active_contract
from alphaforge.strategy.config import StrategyConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("alphaforge.backtest.real")


def load_env_token(env_path: Path) -> str | None:
    """Read UPSTOX_ACCESS_TOKEN from .env if present."""
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if token:
        return token
    if env_path.is_file():
        with contextlib.suppress(Exception):
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("UPSTOX_ACCESS_TOKEN="):
                    val = line.split("=", 1)[1].strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    return val
    return None


def fetch_upstox_1m_candles(
    instrument_key: str,
    from_date: datetime,
    to_date: datetime,
    token: str | None = None,
    cache_dir: Path | None = None,
) -> list[list]:
    """
    Fetch 1-minute historical candles from Upstox API in 7-day chunks.
    Returns sorted list of raw candle lists: [timestamp, open, high, low, close, volume, oi].
    """
    encoded_key = urllib.parse.quote(instrument_key)
    all_candles: list[list] = []

    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_key = instrument_key.replace("|", "_").replace(" ", "_")
        cache_file = cache_dir / f"{safe_key}_{from_date.strftime('%Y%m%d')}_{to_date.strftime('%Y%m%d')}_1m.json"
        if cache_file.is_file():
            logger.info("Loading cached market candles from %s", cache_file)
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, list) and cached:
                    return cached
            except Exception as e:
                logger.warning("Failed to read cache file: %s", e)

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlphaForge/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    curr_start = from_date
    chunk_size = timedelta(days=7)

    logger.info("Fetching real market data for %s from %s to %s...", instrument_key, from_date.date(), to_date.date())

    while curr_start < to_date:
        curr_end = min(curr_start + chunk_size, to_date)
        from_str = curr_start.strftime("%Y-%m-%d")
        to_str = curr_end.strftime("%Y-%m-%d")

        url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/1minute/{to_str}/{from_str}"
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                chunk_candles = data.get("data", {}).get("candles", [])
                logger.info("  Fetched chunk [%s -> %s]: %d candles", from_str, to_str, len(chunk_candles))
                all_candles.extend(chunk_candles)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning("  HTTP %d on chunk [%s -> %s]: %s", e.code, from_str, to_str, err_body[:120])
        except Exception as e:
            logger.warning("  Error on chunk [%s -> %s]: %s", from_str, to_str, e)

        curr_start = curr_end + timedelta(days=1)

    # Upstox returns reverse-chronological; sort chronologically
    dedup: dict[str, list] = {}
    for c in all_candles:
        ts = str(c[0])
        if ts not in dedup:
            dedup[ts] = c

    sorted_candles = sorted(dedup.values(), key=lambda c: c[0])
    logger.info("Total unique 1-minute historical candles fetched: %d", len(sorted_candles))

    if cache_dir and sorted_candles:
        safe_key = instrument_key.replace("|", "_").replace(" ", "_")
        cache_file = cache_dir / f"{safe_key}_{from_date.strftime('%Y%m%d')}_{to_date.strftime('%Y%m%d')}_1m.json"
        with contextlib.suppress(Exception):
            cache_file.write_text(json.dumps(sorted_candles), encoding="utf-8")

    return sorted_candles


def resample_1m_to_interval(
    raw_candles: list[list],
    symbol: str,
    contract_id: str,
    instrument_type: InstrumentType,
    interval_minutes: int,
    filter_regular_session: bool = True,
) -> list[MarketCandle]:
    """
    Resample 1-minute raw candle records into authoritative, clock-aligned MarketCandle instances.
    Enforces:
    1. Regular NSE market hours (09:15 to 15:30 IST = 03:45 to 10:00 UTC) when filter_regular_session=True.
    2. Strict clock-aligned boundaries ((minute // interval_minutes) * interval_minutes).
    3. Session and day boundary isolation (zero cross-day or weekend bar bleeding).
    4. Deterministic chronological sorting and Decimal precision.
    """
    if not raw_candles:
        return []

    sorted_raw = sorted(raw_candles, key=lambda r: str(r[0]))
    aggregated: list[MarketCandle] = []
    chunk: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, int, int]] = []
    current_bucket: datetime | None = None
    tf_str = f"{interval_minutes}m"

    for r in sorted_raw:
        dt = datetime.fromisoformat(str(r[0])).astimezone(UTC)
        t = dt.time()

        # Enforce regular NSE trading session: 03:45 UTC (09:15 IST) <= t < 10:00 UTC (15:30 IST)
        if filter_regular_session and not (time(3, 45) <= t < time(10, 0)):
            continue

        bucket_minute = (dt.minute // interval_minutes) * interval_minutes
        bucket_ts = dt.replace(minute=bucket_minute, second=0, microsecond=0)

        if current_bucket is not None and bucket_ts != current_bucket:
            bar_ts = current_bucket
            bar_open = chunk[0][1]
            bar_high = max(item[2] for item in chunk)
            bar_low = min(item[3] for item in chunk)
            bar_close = chunk[-1][4]
            bar_volume = sum(item[5] for item in chunk)
            bar_oi = chunk[-1][6]

            # OHLC validity safeguard
            if bar_high < max(bar_open, bar_close):
                bar_high = max(bar_open, bar_close)
            if bar_low > min(bar_open, bar_close):
                bar_low = min(bar_open, bar_close)

            mc = MarketCandle(
                symbol=symbol,
                instrument_type=instrument_type,
                contract_id=contract_id,
                exchange_timestamp=bar_ts,
                received_timestamp=bar_ts + timedelta(minutes=interval_minutes),
                timeframe=tf_str,
                open=bar_open,
                high=bar_high,
                low=bar_low,
                close=bar_close,
                volume=max(bar_volume, 1),
                open_interest=bar_oi,
                source="NSE",
            )
            aggregated.append(mc)
            chunk = []

        current_bucket = bucket_ts
        chunk.append((
            dt,
            Decimal(str(r[1])),
            Decimal(str(r[2])),
            Decimal(str(r[3])),
            Decimal(str(r[4])),
            int(r[5]),
            int(r[6]) if len(r) > 6 and r[6] is not None else 0,
        ))

    if chunk and current_bucket is not None:
        bar_ts = current_bucket
        bar_open = chunk[0][1]
        bar_high = max(item[2] for item in chunk)
        bar_low = min(item[3] for item in chunk)
        bar_close = chunk[-1][4]
        bar_volume = sum(item[5] for item in chunk)
        bar_oi = chunk[-1][6]

        if bar_high < max(bar_open, bar_close):
            bar_high = max(bar_open, bar_close)
        if bar_low > min(bar_open, bar_close):
            bar_low = min(bar_open, bar_close)

        mc = MarketCandle(
            symbol=symbol,
            instrument_type=instrument_type,
            contract_id=contract_id,
            exchange_timestamp=bar_ts,
            received_timestamp=bar_ts + timedelta(minutes=interval_minutes),
            timeframe=tf_str,
            open=bar_open,
            high=bar_high,
            low=bar_low,
            close=bar_close,
            volume=max(bar_volume, 1),
            open_interest=bar_oi,
            source="NSE",
        )
        aggregated.append(mc)

    return aggregated


def run_real_backtest(
    days: int = 30,
    symbol: str = "NIFTY",
    instrument_key: str = "NSE_FO|68407",
    contract_id: str = "NIFTY26SEPFUT",
    initial_capital: Decimal = Decimal("1000000"),
    lot_size: int = 50,
    output_report: Path | None = None,
    regime_filter: bool = False,
    min_adx: float = 20.0,
    min_spread: float = 0.0008,
) -> None:
    """Execute historical backtest on real Upstox market data."""
    to_dt = datetime.now(UTC)
    from_dt = to_dt - timedelta(days=days)

    token = load_env_token(_repo_root / ".env")
    cache_dir = _repo_root / "runtime" / "historical_data"

    raw_candles = fetch_upstox_1m_candles(
        instrument_key=instrument_key,
        from_date=from_dt,
        to_date=to_dt,
        token=token,
        cache_dir=cache_dir,
    )

    if not raw_candles:
        logger.error("No historical candles retrieved from Upstox. Check network connectivity or date range.")
        sys.exit(1)

    candles_3m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol=symbol,
        contract_id=contract_id,
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=3,
    )
    candles_15m = resample_1m_to_interval(
        raw_candles=raw_candles,
        symbol=symbol,
        contract_id=contract_id,
        instrument_type=InstrumentType.FUTURES,
        interval_minutes=15,
    )

    logger.info(
        "Resampled %d 1-minute bars into %d 3m execution bars and %d 15m confirmation bars.",
        len(raw_candles), len(candles_3m), len(candles_15m),
    )

    if len(candles_3m) < 30:
        logger.error("Insufficient 3m candles for backtesting (%d bars). Minimum 30 required.", len(candles_3m))
        sys.exit(1)

    # Construct authoritative ContractMaster
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol=symbol,
        contract_id=contract_id,
        instrument_type=InstrumentType.FUTURES,
        listing_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        expiry_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        lot_size=lot_size,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        price_decimal_places=2,
        quantity_decimal_places=0,
        currency="INR",
        data_source="NSE_MASTER",
    )

    dataset_exec = BacktestDataset(dataset_id=f"UPSTOX_REAL_{symbol}_{contract_id}_3M", candles=candles_3m)
    dataset_conf = BacktestDataset(dataset_id=f"UPSTOX_REAL_{symbol}_{contract_id}_15M", candles=candles_15m)

    t_start = candles_3m[0].exchange_timestamp
    t_end = candles_3m[-1].exchange_timestamp + timedelta(minutes=3)

    config = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.1.0",
        dataset_id=dataset_exec.dataset_id,
        start_time=t_start,
        end_time=t_end,
        initial_capital=initial_capital,
        final_position_policy=FinalPositionPolicy.MARK_TO_MARKET,
        warmup_bars=25,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )

    strat_cfg = StrategyConfig(
        strategy_version="1.1.0",
        enable_regime_filter=regime_filter,
        min_adx_threshold=Decimal(str(min_adx)),
        min_ema_spread_pct=Decimal(str(min_spread)),
    )

    # Derivatives / Futures Risk Sizing:
    # 1 lot NIFTY Futures (50 qty * ~25,000 index = ~Rs. 12,50,000 notional).
    # On a Rs. 10 Lakh initial capital account, single position notional is ~1.25x (125%).
    # Set futures risk boundaries so trades are not rejected by cash equity rules:
    risk_cfg = RiskConfig(
        max_single_position_notional=Decimal("2.00"),
        max_portfolio_notional=Decimal("5.00"),
        max_risk_per_trade=Decimal("0.0200"),
        max_portfolio_risk=Decimal("0.0500"),
    )

    logger.info("Launching AlphaForge Pure Deterministic Backtest Engine...")
    logger.info("  Strategy: %s v%s", config.strategy_id, config.strategy_version)
    logger.info("  Regime Filter: %s (Min ADX: %.1f, Min Spread: %.4f)", "ENABLED" if regime_filter else "DISABLED", min_adx, min_spread)
    logger.info("  Confirmation Horizon: 15-minute timeframe (%d bars)", len(candles_15m))
    logger.info("  Initial Capital: Rs. %s", f"{initial_capital:,.2f}")
    logger.info("  Contract: %s (Lot Size: %d)", contract.contract_id, contract.lot_size)
    logger.info("  Time Horizon: %s -> %s", t_start.isoformat(), t_end.isoformat())

    engine = BacktestEngine(
        config=config,
        dataset=dataset_exec,
        contract_master=contract,
        strategy_config=strat_cfg,
        risk_config=risk_cfg,
        confirmation_dataset=dataset_conf,
    )
    result = engine.run()

    # -------------------------------------------------------------
    # Render Institutional Terminal Report
    # -------------------------------------------------------------
    m = result.metrics
    ret_sign = "+" if m.total_return >= 0 else ""
    pf_str = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "N/A"
    payoff_str = f"{m.payoff_ratio:.2f}" if m.payoff_ratio is not None else "N/A"

    print("\n" + "=" * 78)
    print("       ALPHAFORGE QUANTITATIVE OPERATIONS — REAL MARKET BACKTEST")
    print("=" * 78)
    print(f" Run Identifier   : {result.backtest_run_id}")
    print(f" Strategy         : {config.strategy_id} v{config.strategy_version}")
    print(f" Instrument       : {symbol} Futures ({contract_id})")
    print(f" Simulation Window: {t_start.strftime('%Y-%m-%d %H:%M UTC')} to {t_end.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f" Total Candles    : {len(candles_3m):,} bars (3-minute timeframe)")
    print(f" Starting Capital : Rs. {initial_capital:,.2f}")
    print("-" * 78)
    print(" KEY PERFORMANCE INDICATORS (KPIs)")
    print("-" * 78)
    print(f"  Net Realized Return : {ret_sign}Rs. {m.total_return:,.2f} ({ret_sign}{m.total_return_pct:.2f}%)")
    print(f"  Total Closed Trades : {m.total_trades}")
    print(f"  Winning Trades      : {m.winning_trades} ({m.win_rate * 100:.1f}%)")
    print(f"  Losing Trades       : {m.losing_trades} ({m.loss_rate * 100:.1f}%)")
    print(f"  Break-Even Trades   : {m.break_even_trades}")
    print(f"  Profit Factor       : {pf_str}")
    print(f"  Payoff Ratio        : {payoff_str}")
    print(f"  Average Win         : Rs. {m.average_win:,.2f}")
    print(f"  Average Loss        : Rs. {m.average_loss:,.2f}")
    print(f"  Max Drawdown (Peak) : Rs. {m.max_drawdown:,.2f} ({m.max_drawdown_pct:.2f}%)")
    print(f"  Risk Gate Checks    : {engine.risk_evaluations_count} evaluations | {engine.risk_approvals_count} approved | {engine.risk_rejections_recorded} rejected")
    print("-" * 78)

    if result.trades:
        print(" TRADE EXECUTION LOG (AUTHORITATIVE)")
        print("-" * 78)
        header = f"{'#':<3} {'Side':<5} {'Entry Time':<16} {'Entry Px':<10} {'Exit Time':<16} {'Exit Px':<10} {'Net PnL (Rs.)':<14} {'Reason'}"
        print(header)
        print("-" * 78)
        for idx, tr in enumerate(result.trades, start=1):
            side_str = tr.side.value
            entry_ts = tr.entry_timestamp.strftime("%m-%d %H:%M")
            exit_ts = tr.exit_timestamp.strftime("%m-%d %H:%M")
            pnl_sign = "+" if tr.net_pnl >= 0 else ""
            pnl_str = f"{pnl_sign}{tr.net_pnl:,.2f}"
            print(f"{idx:<3} {side_str:<5} {entry_ts:<16} {tr.entry_price:<10.2f} {exit_ts:<16} {tr.exit_price:<10.2f} {pnl_str:<14} {tr.exit_reason}")
        print("-" * 78)
    else:
        print(" TRADE LOG: ZERO TRADES TRIGGERED")
        print("  Notice: Strict quantitative gates, trend filters, or volume spikes")
        print("  did not generate actionable trade setups in this specific time window.")
        print("-" * 78)

    print(" QUANTITATIVE QUALITY GATES")
    print("-" * 78)
    for qg in result.quant_gates:
        status_symbol = "[PASS]" if qg.status.value == "PASS" else f"[{qg.status.value}]"
        print(f"  {status_symbol:<8} {qg.gate_id}: {qg.gate_name} — {qg.reason}")
    print("=" * 78 + "\n")

    # Save report
    if output_report is None:
        output_report = _repo_root / "evidence" / "backtest_real_data_report.json"
    output_report.parent.mkdir(parents=True, exist_ok=True)

    report_payload = {
        "run_id": result.backtest_run_id,
        "strategy_id": config.strategy_id,
        "strategy_version": config.strategy_version,
        "contract_id": contract.contract_id,
        "time_start": t_start.isoformat(),
        "time_end": t_end.isoformat(),
        "total_candles_3m": len(candles_3m),
        "total_candles_1m_raw": len(raw_candles),
        "initial_capital": str(initial_capital),
        "metrics": result.metrics.model_dump(mode="json"),
        "trades_count": len(result.trades),
        "trades": [t.model_dump(mode="json") for t in result.trades],
        "quant_gates": [g.model_dump(mode="json") for g in result.quant_gates],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    output_report.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    logger.info("Authoritative report saved to: %s", output_report)


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaForge Real Market Data Backtest Runner")
    parser.add_argument("--days", type=int, default=30, help="Historical lookback window in days (default: 30)")
    parser.add_argument("--symbol", type=str, default="NIFTY", help="Underlying symbol (default: NIFTY)")
    parser.add_argument("--instrument-key", type=str, default="NSE_FO|68407", help="Upstox instrument key (default: NSE_FO|68407)")
    parser.add_argument("--contract-id", type=str, default="NIFTY26SEPFUT", help="Contract ID (default: NIFTY26SEPFUT)")
    parser.add_argument("--capital", type=float, default=1000000.0, help="Initial capital in INR (default: 1,000,000)")
    parser.add_argument("--lot-size", type=int, default=50, help="Futures contract lot size (default: 50)")
    parser.add_argument("--output", type=str, default=None, help="Path to save report JSON")
    parser.add_argument("--regime-filter", action="store_true", help="Enable ADX + EMA spread market regime filter")
    parser.add_argument("--min-adx", type=float, default=20.0, help="Minimum ADX threshold for trending regime (default: 20.0)")
    parser.add_argument("--min-spread", type=float, default=0.0008, help="Minimum EMA spread percentage (default: 0.0008)")

    args = parser.parse_args()
    out_path = Path(args.output) if args.output else None

    run_real_backtest(
        days=args.days,
        symbol=args.symbol,
        instrument_key=args.instrument_key,
        contract_id=args.contract_id,
        initial_capital=Decimal(str(args.capital)),
        lot_size=args.lot_size,
        output_report=out_path,
        regime_filter=args.regime_filter,
        min_adx=args.min_adx,
        min_spread=args.min_spread,
    )


if __name__ == "__main__":
    main()
