"""
Execution Realism & Microstructure Anomaly Engine (Phase 18-H).
Models:
1. Gap-through-stop loss execution (filling at adverse gap open, never theoretical stop).
2. Latency jitter simulation (variable network latency instead of constant).
3. UNKNOWN order state modeling for lost acks (requiring reconciliation resolution).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.execution.enums import OrderSide

if TYPE_CHECKING:
    from alphaforge.data.models import MarketCandle


class OrderAckState(StrEnum):
    """Order acknowledgment state."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    RESOLVED_AFTER_QUERY = "RESOLVED_AFTER_QUERY"


class GapThroughStopModel:
    """
    Simulates realistic fill prices when market opens or ticks past a stop price.
    """

    @staticmethod
    def calculate_stop_fill_price(
        side: OrderSide,  # SELL for long stop, BUY for short stop
        stop_price: Decimal,
        trigger_candle: MarketCandle,
        base_slippage_pct: Decimal = Decimal("0.0002"),
        tick_size: Decimal = Decimal("0.05"),
    ) -> tuple[Decimal, bool, Decimal]:
        """
        Calculates realistic fill price.
        Returns: (fill_price, is_gap_fill, gap_slippage_amount)
        """
        open_price = trigger_candle.open
        half_tick = tick_size / Decimal("2")

        if side == OrderSide.SELL:
            # Long position stop loss: trigger if price falls to or below stop_price
            if open_price < stop_price:
                # Gapped down below stop! Fill at open price minus additional adverse slippage
                slippage = open_price * base_slippage_pct
                raw_fill = open_price - slippage
                # Round down to tick size
                fill_price = (raw_fill / tick_size).quantize(Decimal("1")) * tick_size
                gap_slip = stop_price - fill_price
                return fill_price, True, gap_slip
            # Normal trigger within bar
            slippage = stop_price * base_slippage_pct
            raw_fill = stop_price - slippage
            fill_price = (raw_fill / tick_size).quantize(Decimal("1")) * tick_size
            return fill_price, False, stop_price - fill_price

        else:
            # Short position stop loss: trigger if price rises to or above stop_price
            if open_price > stop_price:
                # Gapped up above stop! Fill at open price plus additional adverse slippage
                slippage = open_price * base_slippage_pct
                raw_fill = open_price + slippage
                fill_price = ((raw_fill + half_tick) / tick_size).quantize(Decimal("1")) * tick_size
                gap_slip = fill_price - stop_price
                return fill_price, True, gap_slip
            # Normal trigger within bar
            slippage = stop_price * base_slippage_pct
            raw_fill = stop_price + slippage
            fill_price = ((raw_fill + half_tick) / tick_size).quantize(Decimal("1")) * tick_size
            return fill_price, False, fill_price - stop_price


class LatencyJitterModel:
    """
    Simulates variable network and gateway latency.
    """

    def __init__(self, min_latency_ms: int = 5, max_latency_ms: int = 65, seed: int = 42) -> None:
        if min_latency_ms <= 0 or max_latency_ms <= min_latency_ms:
            raise DataIntegrityError("Invalid latency bounds")
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms
        self.rng = random.Random(seed)  # noqa: S311

    def sample_latency_ms(self) -> int:
        """Sample a realistic network latency duration."""
        return self.rng.randint(self.min_latency_ms, self.max_latency_ms)

    def apply_jitter(self, base_timestamp: datetime) -> datetime:
        """Apply sampled jitter to timestamp."""
        ms = self.sample_latency_ms()
        return base_timestamp + timedelta(milliseconds=ms)


class AckLossSimulator:
    """
    Simulates gateway acknowledgment packet loss.
    Orders enter UNKNOWN state until explicit reconciliation query resolves them.
    """

    def __init__(self, ack_loss_rate: float = 0.05, seed: int = 99) -> None:
        self.ack_loss_rate = ack_loss_rate
        self.rng = random.Random(seed)  # noqa: S311
        self.in_flight_unknown_orders: dict[str, dict[str, Any]] = {}

    def process_order_submission(
        self,
        order_id: str,
        symbol: str,
        actual_broker_state: str = "FILLED",
    ) -> tuple[OrderAckState, str]:
        """
        Simulate order dispatch. Returns (ack_state, reason).
        """
        roll = self.rng.random()
        if roll < self.ack_loss_rate:
            # Ack was lost in transit! Client enters UNKNOWN state
            self.in_flight_unknown_orders[order_id] = {
                "symbol": symbol,
                "actual_broker_state": actual_broker_state,
                "entered_unknown_at": datetime.now(),
            }
            return (
                OrderAckState.UNKNOWN,
                f"NETWORK_TIMEOUT: Inbound ack for {order_id} lost in transit. "
                "Order in UNKNOWN state.",
            )

        return OrderAckState.ACKNOWLEDGED, f"ORDER_ACK: {order_id} acknowledged by venue."

    def reconcile_unknown_order(self, order_id: str) -> tuple[bool, str]:
        """
        Simulate explicit reconciliation query to venue to resolve UNKNOWN state.
        """
        if order_id not in self.in_flight_unknown_orders:
            return False, f"Order {order_id} not in UNKNOWN tracking pool"

        record = self.in_flight_unknown_orders.pop(order_id)
        resolved_state = record["actual_broker_state"]
        return True, f"RECONCILED: Order {order_id} resolved to {resolved_state} via venue sync."
