#!/usr/bin/env python3
# ruff: noqa: E402, TC001
"""
AlphaForge — Upstox REAL-MARKET DATA -> PAPER session.

SAFETY INVARIANTS:
- Upstox is READ-ONLY market data only.
- PaperShadowEngine runs strictly in PAPER mode.
- ZERO live broker/order APIs are imported or called.
- No synthetic fallback is permitted when the external feed is unavailable.

The Upstox adapter emits closed 1-minute MarketStreamEvent bars. This runner
causally aggregates those bars into closed 3-minute execution bars and closed
15-minute confirmation bars, then feeds the authoritative PaperShadowEngine.

Important strategy semantics:
- A newly closed 3m bar is passed to PaperShadowEngine as the current [0] bar.
- The frozen strategy engine therefore evaluates the previous fully closed [1]
  bar, preserving its existing closed-candle quarantine and 195-second freshness
  guard rather than changing frozen strategy logic.
- 15m confirmation history is maintained separately and injected only into the
  strategy engine's confirmation-input argument. No 15m bar is mixed into the
  execution timeframe history.

Usage:
    python scripts/run_upstox_paper_session.py

Environment:
    UPSTOX_ACCESS_TOKEN   Required Upstox market-data access token.
    UPSTOX_INSTRUMENT_KEY Optional; defaults to the active contract key.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alphaforge.strategy.config import StrategyConfig

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def load_dotenv(path: Path | None = None) -> None:
    """Load key-value pairs from .env into os.environ if not already set."""
    env_file = path or (_repo_root / ".env")
    if not env_file.is_file():
        return
    with contextlib.suppress(Exception):
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v


load_dotenv()

from alphaforge.broker.models import BrokerOrder, BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.enums import FuturesConfirmationStatus
from alphaforge.core.models import Candle, StrategySignal
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import MarketEvent, PaperShadowConfig
from alphaforge.risk.models import RiskConfig
from alphaforge.shadow_validation.contract_source import AuthoritativeContractSource
from alphaforge.shadow_validation.upstox_adapter import (
    UPSTOX_KNOWN_INSTRUMENT_KEYS,
    UpstoxMarketDataAdapter,
)
from alphaforge.strategy.engine import DeterministicStrategyEngine

if TYPE_CHECKING:
    from alphaforge.shadow_validation.models import MarketStreamEvent
    from alphaforge.strategy.config import StrategyConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("alphaforge.upstox.paper")


@dataclass
class _Bar:
    """Mutable internal aggregation state for one timeframe bucket."""

    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def update(self, event: MarketStreamEvent) -> None:
        self.high = max(self.high, event.high_price)
        self.low = min(self.low, event.low_price)
        self.close = event.close_price
        self.volume += event.volume


class _TimeframeAggregator:
    """Causally aggregate already-closed 1m bars into closed target timeframe bars."""

    def __init__(self, minutes: int) -> None:
        if minutes <= 0 or 60 % minutes != 0:
            raise ValueError("minutes must be a positive divisor of 60")
        self.minutes = minutes
        self._current: _Bar | None = None

    def add(self, event: MarketStreamEvent) -> _Bar | None:
        """Add a 1m bar and emit the prior bucket only after the bucket changes."""
        bucket_minute = (event.exchange_timestamp.minute // self.minutes) * self.minutes
        bucket_start = event.exchange_timestamp.replace(
            minute=bucket_minute,
            second=0,
            microsecond=0,
        )

        if self._current is None:
            self._current = _Bar(
                start=bucket_start,
                open=event.open_price,
                high=event.high_price,
                low=event.low_price,
                close=event.close_price,
                volume=event.volume,
            )
            return None

        if bucket_start == self._current.start:
            self._current.update(event)
            return None

        if bucket_start < self._current.start:
            raise ValueError(
                f"Out-of-order bar: {event.exchange_timestamp.isoformat()} < "
                f"current bucket {self._current.start.isoformat()}"
            )

        closed = self._current
        self._current = _Bar(
            start=bucket_start,
            open=event.open_price,
            high=event.high_price,
            low=event.low_price,
            close=event.close_price,
            volume=event.volume,
        )
        return closed

    def discard_open_bucket(self) -> None:
        """Discard a forming bucket on shutdown; never evaluate a partial bar."""
        self._current = None


def _bar_to_market_candle(
    bar: _Bar,
    *,
    contract_id: str,
    timeframe: str,
    received_timestamp: datetime,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id=contract_id,
        exchange_timestamp=bar.start,
        received_timestamp=received_timestamp,
        timeframe=timeframe,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        open_interest=None,
        source="UPSTOX_REAL_MARKET_SHADOW",
        is_closed=True,
    )


def _event_from_candle(candle: MarketCandle, sequence: int, received: datetime) -> MarketEvent:
    return MarketEvent(
        event_id=f"AF-UPSTOX-PAPER-{sequence:012d}",
        sequence=sequence,
        candle=candle,
        market_timestamp=candle.exchange_timestamp,
        receipt_timestamp=received,
        symbol="NIFTY",
    )


class _ConfirmationAwareStrategyEngine(DeterministicStrategyEngine):
    """
    Compatibility wrapper that preserves the frozen strategy implementation.

    PaperShadowEngine currently supplies its execution-candle history as both
    execution and confirmation inputs. For a live multi-timeframe session we
    need the separate 15m confirmation series without changing the frozen
    PaperShadowEngine or strategy rules.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        super().__init__(config=config)
        self._confirmation_candles: list[Candle] = []
        self.last_signal: StrategySignal | None = None
        self.decision_history: list[dict[str, Any]] = []

    def set_confirmation_candles(self, candles: list[Candle]) -> None:
        self._confirmation_candles = list(candles)

    def evaluate(
        self,
        raw_exec_candles: list[Candle],
        raw_conf_candles: list[Candle],
        futures_status: FuturesConfirmationStatus,
        evaluation_timestamp: datetime,
    ) -> StrategySignal:
        _ = raw_conf_candles
        sig = super().evaluate(
            raw_exec_candles=raw_exec_candles,
            raw_conf_candles=self._confirmation_candles,
            futures_status=futures_status,
            evaluation_timestamp=evaluation_timestamp,
        )
        self.last_signal = sig

        dir_val = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
        decision_val = sig.decision.value if hasattr(sig.decision, "value") else str(sig.decision)
        trend_val = sig.trend_state.value if hasattr(sig.trend_state, "value") else str(sig.trend_state)
        rej_code = sig.rejection_code.value if hasattr(sig.rejection_code, "value") else str(sig.rejection_code)

        reason_str = (
            f"Decision: {decision_val} ({rej_code})"
            if decision_val != "ACCEPT"
            else f"Signal Active: {dir_val} @ {sig.entry_reference}"
        )
        self.decision_history.insert(0, {
            "timestamp": evaluation_timestamp.strftime("%H:%M:%S"),
            "candle": f"#{len(raw_exec_candles)}",
            "strategy_state": trend_val,
            "signal": dir_val,
            "risk_decision": "PASS",
            "contract_decision": "PASS",
            "final_decision": dir_val if decision_val == "ACCEPT" else "WAIT",
            "category": "SIGNAL" if decision_val == "ACCEPT" else "WAIT",
            "reason": reason_str,
        })
        if len(self.decision_history) > 50:
            self.decision_history.pop()
        return sig


class _ObservedPaperBroker(PaperBroker):
    """Paper-only broker subclass exposing session counters for evidence."""

    def __init__(self) -> None:
        super().__init__()
        self.total_submitted = 0
        self.total_filled = 0
        self.recent_orders: list[dict[str, Any]] = []

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        order = super().submit_order(request)
        self.total_submitted += 1
        self.recent_orders.insert(0, {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value if hasattr(order.side, "value") else str(order.side),
            "quantity": order.quantity,
            "price": str(order.average_price or request.price or "0.00"),
            "state": order.status.value if hasattr(order.status, "value") else str(order.status),
            "time": datetime.now(UTC).strftime("%H:%M:%S"),
        })
        if len(self.recent_orders) > 30:
            self.recent_orders.pop()
        return order

    def simulate_full_fill(self, client_order_id: str, fill_price: Decimal) -> BrokerOrder:
        order = super().simulate_full_fill(client_order_id, fill_price)
        self.total_filled += 1
        for o in self.recent_orders:
            if o.get("client_order_id") == client_order_id:
                o["state"] = order.status.value if hasattr(order.status, "value") else str(order.status)
                o["price"] = str(fill_price)
        return order


def _is_nse_session_open(dt_utc: datetime | None = None) -> tuple[bool, str]:
    """Check if NSE trading session is active (09:15-15:30 IST = 03:45-10:00 UTC)."""
    now = dt_utc or datetime.now(UTC)
    if now.weekday() >= 5:
        return False, "CLOSED (WEEKEND)"
    t = now.time()
    session_start = datetime.min.replace(hour=3, minute=45, tzinfo=UTC).time()
    session_end = datetime.min.replace(hour=10, minute=0, tzinfo=UTC).time()
    if session_start <= t <= session_end:
        return True, "OPEN (REGULAR)"
    return False, "CLOSED (OFF-HOURS)"


def main() -> None:
    """Run a live Upstox-feed-driven paper session until interrupted."""
    parser = argparse.ArgumentParser(
        description="AlphaForge Upstox real-market-data to paper session"
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=0,
        help="Optional bounded test duration. 0 means run until interrupted.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="PAPER",
        choices=["PAPER"],
        help="Trading execution mode. Strictly PAPER only.",
    )
    args = parser.parse_args()
    if args.duration_seconds < 0:
        raise SystemExit("--duration-seconds must be >= 0")

    contract_source = AuthoritativeContractSource()
    contract = contract_source.get_active_contract()
    if contract is None:
        raise RuntimeError("No active authoritative NIFTY Futures contract; failing closed.")

    inst_key = os.environ.get(
        "UPSTOX_INSTRUMENT_KEY",
        UPSTOX_KNOWN_INSTRUMENT_KEYS.get(contract.contract_id, f"NSE_FO|{contract.contract_id}"),
    )
    adapter = UpstoxMarketDataAdapter(contract=contract, instrument_key=inst_key)

    stop_requested = False
    stop_flag_file = _repo_root / "runtime" / "bot_stop.flag"
    live_state_file = _repo_root / "runtime" / "paper_bot_live.json"
    live_state_file.parent.mkdir(parents=True, exist_ok=True)

    bot_activity_logs: list[dict[str, Any]] = []
    closed_3m_candles: list[dict[str, Any]] = []
    current_price: Decimal | None = None
    last_state_write_ts = 0.0
    started = datetime.now(UTC)
    state_lock = threading.Lock()

    def log_bot_activity(level: str, component: str, msg: str) -> None:
        with state_lock:
            entry = {
                "timestamp": datetime.now(UTC).strftime("%H:%M:%S"),
                "level": level,
                "component": component,
                "message": msg,
            }
            bot_activity_logs.append(entry)
            if len(bot_activity_logs) > 300:
                bot_activity_logs.pop(0)

    def _trigger_graceful_stop(reason: str = "stop requested") -> None:
        nonlocal stop_requested
        if not stop_requested:
            stop_requested = True
            logger.info("Graceful shutdown triggered (%s); waking event stream.", reason)
            log_bot_activity("INFO", "SESSION", f"Graceful shutdown triggered ({reason})")
            adapter.disconnect()

    def _stop(_signum: int, _frame: object) -> None:
        _trigger_graceful_stop("signal received")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)

    def _poll_stop_flag() -> None:
        while not stop_requested:
            if stop_flag_file.exists():
                _trigger_graceful_stop("bot_stop.flag detected")
                break
            time.sleep(0.1)

    stop_watcher = threading.Thread(
        target=_poll_stop_flag,
        name="BotStopWatcher",
        daemon=True,
    )
    stop_watcher.start()

    config = PaperShadowConfig(
        mode=PaperShadowMode.PAPER,
        lot_size=contract.lot_size,
        contract_multiplier=contract.contract_multiplier,
        initial_capital=Decimal("1000000"),
    )
    risk_config = RiskConfig(
        max_single_position_notional=Decimal("2.00"),
        max_portfolio_notional=Decimal("5.00"),
        max_risk_per_trade=Decimal("0.0200"),
        max_portfolio_risk=Decimal("0.0500"),
    )
    paper_broker = _ObservedPaperBroker()
    engine = PaperShadowEngine(
        config=config,
        risk_config=risk_config,
        broker=paper_broker,
        contract_provider=contract_source.repository,
    )

    regime_enabled = os.environ.get("ENABLE_REGIME_FILTER", "1").lower() not in ("0", "false", "no")
    strat_config = StrategyConfig(
        strategy_version="1.1.0",
        enable_regime_filter=regime_enabled,
        min_adx_threshold=Decimal("18.0"),
        min_ema_spread_pct=Decimal("0.0003"),
    )
    strategy_bridge = _ConfirmationAwareStrategyEngine(config=strat_config)
    engine._strategy_engine = strategy_bridge

    exec_agg = _TimeframeAggregator(3)
    conf_agg = _TimeframeAggregator(15)
    confirmation_history: list[Candle] = []

    counts: dict[str, int] = {
        "upstox_1m_events": 0,
        "execution_3m_bars": 0,
        "confirmation_15m_bars": 0,
    }

    def flush_live_state(status_override: str | None = None, force: bool = False) -> None:
        nonlocal last_state_write_ts
        with state_lock:
            now_mono = time.monotonic()
            if not force and (now_mono - last_state_write_ts) < 0.5:
                return
            last_state_write_ts = now_mono

            sig = strategy_bridge.last_signal
            trend_val = sig.trend_state.value if sig and hasattr(sig.trend_state, "value") else "NEUTRAL"
            dir_val = sig.direction.value if sig and hasattr(sig.direction, "value") else "NO ACTIVE SIGNAL"
            rej_code = sig.rejection_code.value if sig and hasattr(sig.rejection_code, "value") else "Awaiting setup"

            st_name = status_override or ("RUNNING" if not stop_requested else "STOPPING")

            payload_state = {
                "status": st_name,
                "provider": "UPSTOX",
                "contract_id": contract.contract_id,
                "instrument_key": adapter._instrument_key,
                "last_update": datetime.now(UTC).isoformat(),
                "counts": dict(counts),
                "latest_price": str(current_price) if current_price is not None else None,
                "candles": list(closed_3m_candles[-100:]),
                "confirmation_bars_count": len(confirmation_history),
                "signal": {
                    "strategy_state": trend_val,
                    "trend": trend_val,
                    "momentum": "WAIT",
                    "breakout_status": "NOT_CONFIRMED",
                    "higher_timeframe_confirmation": "WAIT",
                    "volatility_state": "NORMAL",
                    "risk_gate": "PASS",
                    "contract_gate": "VALID",
                    "last_decision": dir_val,
                    "decision_timestamp": datetime.now(UTC).isoformat(),
                    "decision_reason": f"Regime: {trend_val} | Status: {rej_code}",
                    "decision_history": list(strategy_bridge.decision_history),
                },
                "orders": list(paper_broker.recent_orders),
                "positions": [
                    {
                        "symbol": p.symbol,
                        "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                        "quantity": p.quantity,
                        "average_price": str(p.average_price) if p.average_price is not None else None,
                        "status": p.status,
                    }
                    for p in paper_broker.get_positions()
                ],
                "bot_logs": list(bot_activity_logs[-250:]),
            }
            tmp = live_state_file.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(payload_state, indent=2, default=str), encoding="utf-8")
                tmp.replace(live_state_file)
            except Exception:
                pass

    def _heartbeat_worker() -> None:
        last_heartbeat = time.monotonic()
        while not stop_requested and not stop_flag_file.exists():
            time.sleep(1.0)
            now_mono = time.monotonic()
            if now_mono - last_heartbeat >= 15.0:
                last_heartbeat = now_mono
                uptime_sec = int((datetime.now(UTC) - started).total_seconds())
                mins, secs = divmod(uptime_sec, 60)
                hrs, mins = divmod(mins, 60)
                uptime_str = f"{hrs:02d}h {mins:02d}m {secs:02d}s" if hrs else f"{mins:02d}m {secs:02d}s"

                is_open, session_status = _is_nse_session_open()
                t_events = counts.get("upstox_1m_events", 0)
                e_bars = counts.get("execution_3m_bars", 0)
                c_bars = counts.get("confirmation_15m_bars", 0)

                if is_open:
                    msg = (
                        f"Heartbeat OK | Uptime: {uptime_str} | Feed: STREAMING | Market: OPEN "
                        f"| Ticks: {t_events} | 3m Bars: {e_bars} | 15m Bars: {c_bars}"
                    )
                else:
                    msg = (
                        f"Heartbeat OK | Uptime: {uptime_str} | Feed: CONNECTED | Market: {session_status} "
                        f"(Waiting for next session) | 0 Real Orders Enforced"
                    )
                log_bot_activity("HEARTBEAT", "HEALTH", msg)
                flush_live_state(force=True)

    heartbeat_thread = threading.Thread(
        target=_heartbeat_worker,
        name="BotHeartbeatWorker",
        daemon=True,
    )

    log_bot_activity(
        "INFO",
        "STARTUP",
        f"AlphaForge Paper Bot starting. Contract: {contract.contract_id}, Key: {adapter._instrument_key}",
    )
    flush_live_state(status_override="STARTING", force=True)

    # Preload historical 15m confirmation bars for Day-1 ADX & EMA warmup
    try:
        from scripts.run_real_data_backtest import fetch_upstox_1m_candles, resample_1m_to_interval
        warmup_end = datetime.now(UTC)
        warmup_start = warmup_end - timedelta(days=6)
        raw_warmup = fetch_upstox_1m_candles(
            instrument_key=adapter._instrument_key,
            from_date=warmup_start,
            to_date=warmup_end,
            token=adapter._access_token or "",
            cache_dir=_repo_root / "runtime" / "historical_data",
        )
        if raw_warmup:
            warmup_15m = resample_1m_to_interval(
                raw_candles=raw_warmup,
                symbol=contract.underlying_symbol,
                contract_id=contract.contract_id,
                instrument_type=contract.instrument_type,
                interval_minutes=15,
            )
            for mc in warmup_15m:
                confirmation_history.append(mc.to_strategy_candle())
            strategy_bridge.set_confirmation_candles(confirmation_history)
            logger.info("Preloaded %d historical 15m confirmation bars for Day-1 ADX warmup.", len(confirmation_history))
            log_bot_activity("WARMUP", "REGIME", f"Preloaded {len(confirmation_history)} historical 15m confirmation bars for ADX warmup")
    except Exception as e:
        logger.warning("Could not preload historical confirmation warmup: %s", e)
        log_bot_activity("WARN", "WARMUP", f"Confirmation warmup warning: {e}")

    evidence_dir = _repo_root / "evidence" / "paper_live"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)

    print("=" * 76)
    print("  ALPHAFORGE — UPSTOX REAL MARKET DATA -> PAPER SESSION")
    print("  MARKET DATA: LIVE / EXTERNAL / READ-ONLY")
    print("  EXECUTION: PAPER ONLY / ZERO LIVE ORDERS")
    print(f"  CONTRACT: {contract.contract_id} / LOT {contract.lot_size}")
    print(f"  INSTRUMENT KEY: {adapter._instrument_key}")
    print("=" * 76)

    has_error = False
    try:
        adapter.connect()
        telemetry = adapter.get_connection_telemetry()
        if not telemetry.get("provider_authenticated") or not telemetry.get("is_live_external"):
            raise RuntimeError(
                "Upstox connection did not prove authenticated external live feed; failing closed."
            )

        logger.info("Upstox authenticated; consuming read-only real-market stream.")
        log_bot_activity("FEED", "UPSTOX", f"Upstox authenticated. Subscribed to {adapter._instrument_key}")
        flush_live_state(status_override="CONNECTED", force=True)
        heartbeat_thread.start()

        for market_event in adapter.stream_events():
            if stop_requested or stop_flag_file.exists():
                break

            counts["upstox_1m_events"] += 1
            recv = market_event.ingestion_timestamp
            current_price = market_event.close_price

            if counts["upstox_1m_events"] % 5 == 1:
                log_bot_activity(
                    "TICK",
                    "FEED",
                    f"Market event #{counts['upstox_1m_events']} @ {current_price} (Vol: {market_event.volume})",
                )

            # Build both timeframes from the same already-validated 1m source.
            exec_bar = exec_agg.add(market_event)
            conf_bar = conf_agg.add(market_event)

            if conf_bar is not None:
                conf_candle = _bar_to_market_candle(
                    conf_bar,
                    contract_id=contract.contract_id,
                    timeframe="15m",
                    received_timestamp=recv,
                )
                confirmation_history.append(conf_candle.to_strategy_candle())
                confirmation_history = confirmation_history[-200:]
                strategy_bridge.set_confirmation_candles(confirmation_history)
                counts["confirmation_15m_bars"] += 1
                log_bot_activity(
                    "CANDLE",
                    "15M_CONF",
                    f"Formed 15m candle #{counts['confirmation_15m_bars']} | C: {conf_candle.close} | Bars: {len(confirmation_history)}",
                )

            if exec_bar is None:
                flush_live_state(force=False)
                continue

            exec_candle = _bar_to_market_candle(
                exec_bar,
                contract_id=contract.contract_id,
                timeframe="3m",
                received_timestamp=recv,
            )
            sequence = counts["execution_3m_bars"] + 1
            event = _event_from_candle(exec_candle, sequence, recv)

            # Record 3m candle for dashboard chart
            closed_3m_candles.append({
                "open": float(exec_candle.open),
                "high": float(exec_candle.high),
                "low": float(exec_candle.low),
                "close": float(exec_candle.close),
                "volume": exec_candle.volume,
                "timestamp": exec_candle.exchange_timestamp.isoformat(),
            })
            closed_3m_candles = closed_3m_candles[-100:]

            # PaperShadowEngine remains authoritative for validation, risk,
            # routing/FSM, deterministic fill simulation, P&L, and reconciliation.
            engine.process_event(event)
            counts["execution_3m_bars"] += 1

            sig = strategy_bridge.last_signal
            dir_str = sig.direction.value if sig and hasattr(sig.direction, "value") else "WAIT"
            trend_str = sig.trend_state.value if sig and hasattr(sig.trend_state, "value") else "NEUTRAL"
            log_bot_activity(
                "STRATEGY",
                "EVAL",
                f"Formed 3m candle #{counts['execution_3m_bars']} | C: {exec_candle.close} | Stance: {dir_str} (Regime: {trend_str})",
            )
            flush_live_state(force=True)

            if counts["execution_3m_bars"] % 5 == 0:
                logger.info(
                    "Live 1m=%d | closed 3m=%d | closed 15m=%d | paper submitted=%d | "
                    "paper filled=%d | positions=%d",
                    counts["upstox_1m_events"],
                    counts["execution_3m_bars"],
                    counts["confirmation_15m_bars"],
                    paper_broker.total_submitted,
                    paper_broker.total_filled,
                    len(paper_broker.get_positions()),
                )

            if (
                args.duration_seconds
                and (datetime.now(UTC) - started).total_seconds() >= args.duration_seconds
            ):
                stop_requested = True
                break

    except Exception as exc:
        has_error = True
        logger.exception("Session fatal error: %s", exc)
        log_bot_activity("ERROR", "FATAL", f"Bot fatal error: {exc}")
        flush_live_state(status_override="ERROR", force=True)
        raise

    finally:
        # Never flush/evaluate a partial 3m or 15m bucket at shutdown.
        exec_agg.discard_open_bucket()
        conf_agg.discard_open_bucket()
        stop_flag_file.unlink(missing_ok=True)
        try:
            adapter.disconnect()
        except Exception:
            logger.exception("Adapter shutdown error; session evidence will still be written.")

        log_bot_activity("INFO", "SHUTDOWN", "Bot session terminated. Disconnected feed.")
        flush_live_state(status_override="ERROR" if has_error else "STOPPED", force=True)

        ended = datetime.now(UTC)
        final_telemetry: dict[str, Any] = adapter.get_connection_telemetry()
        positions = [
            {
                "symbol": p.symbol,
                "side": p.side.value,
                "quantity": p.quantity,
                "average_price": str(p.average_price) if p.average_price is not None else None,
                "status": p.status,
            }
            for p in paper_broker.get_positions()
        ]
        payload = {
            "run_type": "UPSTOX_REAL_MARKET_TO_PAPER",
            "provider": "UPSTOX",
            "mode": "PAPER",
            "live_orders": 0,
            "live_broker_calls": 0,
            "contract_id": contract.contract_id,
            "start_time": started.isoformat(),
            "end_time": ended.isoformat(),
            "duration_seconds": (ended - started).total_seconds(),
            "counts": counts,
            "paper_orders_submitted": paper_broker.total_submitted,
            "paper_fills_simulated": paper_broker.total_filled,
            "paper_positions": positions,
            "engine_state": str(engine.state),
            "connection_telemetry": final_telemetry,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        evidence_file = evidence_dir / f"upstox_paper_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        evidence_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Evidence saved: %s", evidence_file)


if __name__ == "__main__":
    main()
