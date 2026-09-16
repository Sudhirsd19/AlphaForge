#!/usr/bin/env python3
"""
AlphaForge — Upstox Real-Market Data -> PAPER execution session.

SAFETY:
- Upstox is READ-ONLY market data only.
- PaperShadowEngine runs strictly in PAPER mode.
- ZERO live broker/order APIs are imported or called.
- No synthetic fallback is allowed when the live feed is unavailable.

The Upstox adapter emits closed 1-minute MarketStreamEvent bars. This runner
causally aggregates those bars into closed 3-minute execution bars and closed
15-minute confirmation bars, then feeds the authoritative PaperShadowEngine.

Usage:
    python scripts/run_upstox_paper_session.py

Environment:
    UPSTOX_ACCESS_TOKEN   Required Upstox market-data access token.
    UPSTOX_INSTRUMENT_KEY Optional; defaults to the active contract key.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from alphaforge.broker.paper import PaperBroker
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import MarketEvent, PaperShadowConfig
from alphaforge.shadow_validation.contract_source import AuthoritativeContractSource
from alphaforge.shadow_validation.upstox_adapter import UpstoxMarketDataAdapter

if TYPE_CHECKING:
    from alphaforge.shadow_validation.models import MarketStreamEvent

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
        """Add a closed 1m event; emit the previous bucket only when a new bucket starts."""
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
            # Out-of-order data must never mutate an already-progressing bucket.
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

    def flush(self) -> _Bar | None:
        """Return current bucket for explicit session shutdown; caller decides whether to use it."""
        current = self._current
        self._current = None
        return current


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


def main() -> None:
    """Run a live Upstox-feed-driven paper session until interrupted."""
    stop_requested = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        logger.info("Shutdown requested; finishing current event safely...")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    contract = AuthoritativeContractSource().get_active_contract()
    if contract is None:
        raise RuntimeError("No active authoritative NIFTY Futures contract; failing closed.")

    paper_config = PaperShadowConfig(
        mode=PaperShadowMode.PAPER,
        lot_size=contract.lot_size,
        contract_multiplier=contract.contract_multiplier,
        initial_capital=Decimal("1000000"),
    )
    paper_broker = PaperBroker()
    engine = PaperShadowEngine(
        config=paper_config,
        broker=paper_broker,
        contract_provider=AuthoritativeContractSource().repository,
    )

    adapter = UpstoxMarketDataAdapter(contract=contract)
    exec_agg = _TimeframeAggregator(3)
    conf_agg = _TimeframeAggregator(15)
    confirmation_history: list = []

    evidence_dir = _repo_root / "evidence" / "paper_live"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    counts = {
        "upstox_events": 0,
        "execution_3m_bars": 0,
        "confirmation_15m_bars": 0,
        "engine_events": 0,
        "accepted_signals": 0,
        "paper_orders": 0,
        "paper_fills": 0,
    }

    print("=" * 76)
    print("  ALPHAFORGE — UPSTOX REAL MARKET DATA -> PAPER SESSION")
    print("  MARKET DATA: LIVE / EXTERNAL / READ-ONLY")
    print("  EXECUTION: PAPER ONLY / ZERO LIVE ORDERS")
    print(f"  CONTRACT: {contract.contract_id} / LOT {contract.lot_size}")
    print("=" * 76)

    try:
        adapter.connect()
        logger.info("Upstox authorized; starting read-only live stream...")

        before_orders = 0
        before_fills = 0
        sequence = 0

        for market_event in adapter.stream_events():
            if stop_requested:
                break

            counts["upstox_events"] += 1
            recv = market_event.ingestion_timestamp

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
                counts["confirmation_15m_bars"] += 1

            if exec_bar is None:
                continue

            exec_candle = _bar_to_market_candle(
                exec_bar,
                contract_id=contract.contract_id,
                timeframe="3m",
                received_timestamp=recv,
            )
            sequence += 1
            event = _event_from_candle(exec_candle, sequence, recv)

            # Keep only confirmation bars whose close/boundary timestamp is causal
            # for the execution trigger. Strategy engine performs its own <= filter too.
            causal_conf = [c for c in confirmation_history if c.timestamp <= exec_candle.exchange_timestamp]
            engine.process_event(event, confirmation_candles=causal_conf)
            counts["execution_3m_bars"] += 1
            counts["engine_events"] += 1

            orders = paper_broker.get_orders()
            fills = paper_broker.get_fills()
            counts["paper_orders"] = len(orders)
            counts["paper_fills"] = len(fills)

            if len(orders) > before_orders:
                logger.info(
                    "PAPER ORDER GENERATED — total=%d (LIVE ORDERS=0)",
                    len(orders),
                )
                before_orders = len(orders)
            if len(fills) > before_fills:
                logger.info(
                    "PAPER FILL SIMULATED — total=%d (LIVE ORDERS=0)",
                    len(fills),
                )
                before_fills = len(fills)

            if counts["engine_events"] % 10 == 0:
                logger.info(
                    "Live bars=%d | 3m=%d | 15m=%d | paper_orders=%d | paper_fills=%d",
                    counts["upstox_events"],
                    counts["execution_3m_bars"],
                    counts["confirmation_15m_bars"],
                    counts["paper_orders"],
                    counts["paper_fills"],
                )

    finally:
        with _suppress_all():
            adapter.disconnect()

        ended = datetime.now(UTC)
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
            "engine_state": str(engine.state),
            "connection_telemetry": adapter.get_connection_telemetry(),
            "generated_at": datetime.now(UTC).isoformat(),
        }
        evidence_file = evidence_dir / f"upstox_paper_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        evidence_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Evidence saved: %s", evidence_file)


class _suppress_all:
    """Tiny local context manager to keep shutdown fail-closed and non-destructive."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return True


if __name__ == "__main__":
    main()
