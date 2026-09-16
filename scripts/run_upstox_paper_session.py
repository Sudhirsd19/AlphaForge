#!/usr/bin/env python3
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
import json
import logging
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrder
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.models import Candle
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import MarketEvent, PaperShadowConfig
from alphaforge.shadow_validation.contract_source import AuthoritativeContractSource
from alphaforge.shadow_validation.upstox_adapter import UpstoxMarketDataAdapter
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
    Thin compatibility wrapper that preserves the frozen strategy implementation.

    PaperShadowEngine historically supplies its execution-candle history as both
    execution and confirmation inputs. For a real multi-timeframe session we need
    the separate 15m confirmation series without altering the PaperShadowEngine or
    the frozen strategy rules themselves.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        super().__init__(config=config)
        self._confirmation_candles: list[Candle] = []

    def set_confirmation_candles(self, candles: list[Candle]) -> None:
        self._confirmation_candles = list(candles)

    def evaluate(
        self,
        raw_exec_candles: list[Candle],
        raw_conf_candles: list[Candle],
        futures_status,
        evaluation_timestamp: datetime,
    ):
        # The base PaperShadowEngine argument is intentionally ignored here.
        # Only the runner-maintained 15m series is used for confirmation.
        return super().evaluate(
            raw_exec_candles=raw_exec_candles,
            raw_conf_candles=self._confirmation_candles,
            futures_status=futures_status,
            evaluation_timestamp=evaluation_timestamp,
        )


class _ObservedPaperBroker(PaperBroker):
    """Paper-only broker subclass exposing session counters for evidence."""

    def __init__(self) -> None:
        super().__init__()
        self.total_submitted = 0
        self.total_filled = 0

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        order = super().submit_order(request)
        self.total_submitted += 1
        return order

    def simulate_full_fill(self, client_order_id: str, fill_price: Decimal) -> BrokerOrder:
        order = super().simulate_full_fill(client_order_id, fill_price)
        self.total_filled += 1
        return order


def main() -> None:
    """Run a live Upstox-feed-driven paper session until interrupted."""
    parser = argparse.ArgumentParser(description="AlphaForge Upstox real-market-data to paper session")
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=0,
        help="Optional bounded test duration. 0 means run until interrupted.",
    )
    args = parser.parse_args()
    if args.duration_seconds < 0:
        raise SystemExit("--duration-seconds must be >= 0")

    stop_requested = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        logger.info("Shutdown requested; partial timeframe buckets will NOT be evaluated.")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    contract_source = AuthoritativeContractSource()
    contract = contract_source.get_active_contract()
    if contract is None:
        raise RuntimeError("No active authoritative NIFTY Futures contract; failing closed.")

    config = PaperShadowConfig(
        mode=PaperShadowMode.PAPER,
        lot_size=contract.lot_size,
        contract_multiplier=contract.contract_multiplier,
        initial_capital=Decimal("1000000"),
    )
    paper_broker = _ObservedPaperBroker()
    engine = PaperShadowEngine(
        config=config,
        broker=paper_broker,
        contract_provider=contract_source.repository,
    )

    # Preserve PaperShadowEngine orchestration while injecting the real 15m
    # confirmation timeframe into the frozen DeterministicStrategyEngine.
    strategy_bridge = _ConfirmationAwareStrategyEngine()
    engine._strategy_engine = strategy_bridge

    adapter = UpstoxMarketDataAdapter(contract=contract)
    exec_agg = _TimeframeAggregator(3)
    conf_agg = _TimeframeAggregator(15)
    confirmation_history: list[Candle] = []

    evidence_dir = _repo_root / "evidence" / "paper_live"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    counts = {
        "upstox_1m_events": 0,
        "execution_3m_bars": 0,
        "confirmation_15m_bars": 0,
    }

    print("=" * 76)
    print("  ALPHAFORGE — UPSTOX REAL MARKET DATA -> PAPER SESSION")
    print("  MARKET DATA: LIVE / EXTERNAL / READ-ONLY")
    print("  EXECUTION: PAPER ONLY / ZERO LIVE ORDERS")
    print(f"  CONTRACT: {contract.contract_id} / LOT {contract.lot_size}")
    print("=" * 76)

    try:
        adapter.connect()
        telemetry = adapter.get_connection_telemetry()
        if not telemetry.get("provider_authenticated") or not telemetry.get("is_live_external"):
            raise RuntimeError("Upstox connection did not prove authenticated external live feed; failing closed.")

        logger.info("Upstox authenticated; consuming read-only real-market stream.")

        for market_event in adapter.stream_events():
            if stop_requested:
                break

            counts["upstox_1m_events"] += 1
            recv = market_event.ingestion_timestamp

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

            if exec_bar is None:
                continue

            exec_candle = _bar_to_market_candle(
                exec_bar,
                contract_id=contract.contract_id,
                timeframe="3m",
                received_timestamp=recv,
            )
            sequence = counts["execution_3m_bars"] + 1
            event = _event_from_candle(exec_candle, sequence, recv)

            # PaperShadowEngine remains authoritative for validation, risk,
            # routing/FSM, deterministic fill simulation, P&L, and reconciliation.
            # It will only see 3m execution candles; the wrapper supplies 15m
            # confirmation candles to the strategy engine.
            engine.process_event(event)
            counts["execution_3m_bars"] += 1

            if counts["execution_3m_bars"] % 5 == 0:
                logger.info(
                    "Live 1m=%d | closed 3m=%d | closed 15m=%d | paper submitted=%d | paper filled=%d | positions=%d",
                    counts["upstox_1m_events"],
                    counts["execution_3m_bars"],
                    counts["confirmation_15m_bars"],
                    paper_broker.total_submitted,
                    paper_broker.total_filled,
                    len(paper_broker.get_positions()),
                )

            if args.duration_seconds and (datetime.now(UTC) - started).total_seconds() >= args.duration_seconds:
                stop_requested = True
                break

    finally:
        # Never flush/evaluate a partial 3m or 15m bucket at shutdown.
        exec_agg.discard_open_bucket()
        conf_agg.discard_open_bucket()
        try:
            adapter.disconnect()
        except Exception:
            logger.exception("Adapter shutdown error; session evidence will still be written.")

        ended = datetime.now(UTC)
        final_telemetry = adapter.get_connection_telemetry()
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
