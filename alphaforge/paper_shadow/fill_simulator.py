"""
AlphaForge Deterministic Fill Simulator for Paper / Shadow Trading.

Implements pure deterministic execution simulation for MARKET, LIMIT, STOP_LOSS,
and TAKE_PROFIT orders using frozen Phase 6 cost and slippage models.
Enforces conservative same-bar SL/TP ambiguity resolution (Stop-Loss first).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

from alphaforge.backtest.fills import SimulatedFill
from alphaforge.core.exceptions import ImpossibleFillError, OrderValidationError
from alphaforge.cost.calculator import calculate_effective_price, calculate_fee
from alphaforge.cost.models import CostConfig
from alphaforge.execution.enums import OrderSide
from alphaforge.paper_shadow.enums import FillType, OHLCResolutionPolicy
from alphaforge.risk.enums import TradeSide

if TYPE_CHECKING:
    from alphaforge.data.models import MarketCandle


class BracketEvaluationResult(NamedTuple):
    """Result of resting bracket (Stop-Loss / Take-Profit) evaluation."""

    triggered: bool
    fill: SimulatedFill | None
    bracket_type: str | None


class DeterministicFillSimulator:
    """
    Pure deterministic execution simulator for Paper and Shadow trading.
    Calculates execution fills, volume-weighted prices, fees, and slippage loss.
    """

    def __init__(
        self,
        cost_config: CostConfig | None = None,
        ohlc_policy: OHLCResolutionPolicy = OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE,
        contract_multiplier: Decimal = Decimal("1"),
    ) -> None:
        self._cost_config = cost_config or CostConfig()
        self._ohlc_policy = ohlc_policy
        self._multiplier = contract_multiplier

    @property
    def cost_config(self) -> CostConfig:
        return self._cost_config

    @property
    def ohlc_policy(self) -> OHLCResolutionPolicy:
        return self._ohlc_policy

    @property
    def multiplier(self) -> Decimal:
        return self._multiplier

    def simulate_market_order(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        candle: MarketCandle,
        is_entry: bool = True,
        fill_price_override: Decimal | None = None,
    ) -> SimulatedFill:
        """
        Simulate immediate market order execution against candle open price (or override).
        Applies Phase 6 adverse slippage and transaction fee.
        """
        if quantity <= 0:
            raise OrderValidationError(f"quantity must be positive: {quantity}")

        ref_price = fill_price_override if fill_price_override is not None else candle.open
        if ref_price <= Decimal("0"):
            raise ImpossibleFillError(
                f"Cannot fill market order at non-positive price: {ref_price}"
            )

        trade_side = TradeSide.LONG if side == OrderSide.BUY else TradeSide.SHORT
        slippage_rate = (
            self._cost_config.entry_slippage_rate
            if is_entry
            else self._cost_config.exit_slippage_rate
        )
        fee_rate = self._cost_config.entry_fee_rate if is_entry else self._cost_config.exit_fee_rate

        effective_price = calculate_effective_price(
            reference_price=ref_price,
            slippage_rate=slippage_rate,
            side=trade_side,
            is_entry=is_entry,
        )

        dec_qty = Decimal(quantity)
        notional = effective_price * dec_qty * self._multiplier
        fee = calculate_fee(notional, fee_rate)
        if not is_entry:
            fee += self._cost_config.fixed_cost_per_trade
        slippage_loss = abs(effective_price - ref_price) * dec_qty * self._multiplier

        return SimulatedFill(
            order_id=order_id,
            timestamp=candle.exchange_timestamp,
            symbol=symbol,
            side=trade_side,
            intended_qty=quantity,
            filled_qty=quantity,
            requested_price=ref_price,
            effective_price=effective_price,
            fill_type=FillType.MARKET.value,
            fee=fee,
            slippage_loss=slippage_loss,
            gross_notional=notional,
            reason="MARKET_ORDER_EXECUTED",
        )

    def simulate_limit_order(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        limit_price: Decimal,
        candle: MarketCandle,
        is_entry: bool = True,
    ) -> SimulatedFill | None:
        """
        Simulate resting limit order execution.
        Fills only when market price range is capable of trading at or better than limit:
        - BUY: candle.low <= limit_price
        - SELL: candle.high >= limit_price
        Returns None if market did not touch limit price.
        """
        if limit_price <= Decimal("0"):
            raise OrderValidationError(f"limit_price must be positive: {limit_price}")

        if side == OrderSide.BUY:
            if candle.low > limit_price:
                return None
            # Fill at limit price (or open if market gapped favorably below limit)
            ref_price = min(limit_price, candle.open) if candle.open < limit_price else limit_price
        else:  # SELL
            if candle.high < limit_price:
                return None
            ref_price = max(limit_price, candle.open) if candle.open > limit_price else limit_price

        trade_side = TradeSide.LONG if side == OrderSide.BUY else TradeSide.SHORT
        slippage_rate = (
            self._cost_config.entry_slippage_rate
            if is_entry
            else self._cost_config.exit_slippage_rate
        )
        fee_rate = self._cost_config.entry_fee_rate if is_entry else self._cost_config.exit_fee_rate

        effective_price = calculate_effective_price(
            reference_price=ref_price,
            slippage_rate=slippage_rate,
            side=trade_side,
            is_entry=is_entry,
        )

        dec_qty = Decimal(quantity)
        notional = effective_price * dec_qty * self._multiplier
        fee = calculate_fee(notional, fee_rate)
        if not is_entry:
            fee += self._cost_config.fixed_cost_per_trade
        slippage_loss = abs(effective_price - ref_price) * dec_qty * self._multiplier

        return SimulatedFill(
            order_id=order_id,
            timestamp=candle.exchange_timestamp,
            symbol=symbol,
            side=trade_side,
            intended_qty=quantity,
            filled_qty=quantity,
            requested_price=ref_price,
            effective_price=effective_price,
            fill_type=FillType.LIMIT.value,
            fee=fee,
            slippage_loss=slippage_loss,
            gross_notional=notional,
            reason="LIMIT_ORDER_EXECUTED",
        )

    def evaluate_resting_brackets(
        self,
        order_id: str,
        symbol: str,
        side: TradeSide,
        quantity: int,
        stop_price: Decimal,
        target_price: Decimal,
        candle: MarketCandle,
    ) -> BracketEvaluationResult:
        """
        Evaluate resting stop-loss and take-profit protection against candle range.
        Enforces conservative same-bar SL/TP ambiguity resolution (SL triggered first).
        """
        dec_qty = Decimal(quantity)
        high = candle.high
        low = candle.low
        open_p = candle.open

        sl_hit = False
        tp_hit = False

        if side == TradeSide.LONG:
            sl_hit = low <= stop_price
            tp_hit = high >= target_price
        else:  # SHORT
            sl_hit = high >= stop_price
            tp_hit = low <= target_price

        if not sl_hit and not tp_hit:
            return BracketEvaluationResult(triggered=False, fill=None, bracket_type=None)

        # Ambiguity resolution
        if sl_hit and tp_hit:
            execute_sl = self._ohlc_policy == OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE
        elif sl_hit:
            execute_sl = True
        else:
            execute_sl = False

        if execute_sl:
            # Conservative Stop-Loss execution with gap handling
            if side == TradeSide.LONG:
                ref_exit = open_p if open_p < stop_price else stop_price
            else:
                ref_exit = open_p if open_p > stop_price else stop_price

            effective_exit = calculate_effective_price(
                reference_price=ref_exit,
                slippage_rate=self._cost_config.exit_slippage_rate,
                side=side,
                is_entry=False,
            )
            notional = effective_exit * dec_qty * self._multiplier
            fee = (
                calculate_fee(notional, self._cost_config.exit_fee_rate)
                + self._cost_config.fixed_cost_per_trade
            )
            slippage_loss = abs(effective_exit - ref_exit) * dec_qty * self._multiplier

            fill = SimulatedFill(
                order_id=order_id,
                timestamp=candle.exchange_timestamp,
                symbol=symbol,
                side=side,
                intended_qty=quantity,
                filled_qty=quantity,
                requested_price=ref_exit,
                effective_price=effective_exit,
                fill_type=FillType.STOP_LOSS.value,
                fee=fee,
                slippage_loss=slippage_loss,
                gross_notional=notional,
                reason="RESTING_STOP_LOSS_TRIGGERED",
            )
            return BracketEvaluationResult(triggered=True, fill=fill, bracket_type="STOP_LOSS")

        else:
            # Take-Profit execution
            ref_exit = target_price
            effective_exit = calculate_effective_price(
                reference_price=ref_exit,
                slippage_rate=self._cost_config.exit_slippage_rate,
                side=side,
                is_entry=False,
            )
            notional = effective_exit * dec_qty * self._multiplier
            fee = (
                calculate_fee(notional, self._cost_config.exit_fee_rate)
                + self._cost_config.fixed_cost_per_trade
            )
            slippage_loss = abs(effective_exit - ref_exit) * dec_qty * self._multiplier

            fill = SimulatedFill(
                order_id=order_id,
                timestamp=candle.exchange_timestamp,
                symbol=symbol,
                side=side,
                intended_qty=quantity,
                filled_qty=quantity,
                requested_price=ref_exit,
                effective_price=effective_exit,
                fill_type=FillType.TAKE_PROFIT.value,
                fee=fee,
                slippage_loss=slippage_loss,
                gross_notional=notional,
                reason="RESTING_TAKE_PROFIT_TRIGGERED",
            )
            return BracketEvaluationResult(triggered=True, fill=fill, bracket_type="TAKE_PROFIT")

    def simulate_partial_fill(
        self,
        order_id: str,
        symbol: str,
        side: OrderSide,
        intended_qty: int,
        fill_qty: int,
        candle: MarketCandle,
        is_entry: bool = True,
        fill_price_override: Decimal | None = None,
    ) -> SimulatedFill:
        """
        Simulate a partial fill chunk on an order.
        Validates fill_qty <= intended_qty.
        """
        if fill_qty <= 0 or fill_qty > intended_qty:
            raise OrderValidationError(
                f"fill_qty ({fill_qty}) must be positive and <= intended_qty ({intended_qty})"
            )

        ref_price = fill_price_override if fill_price_override is not None else candle.open
        trade_side = TradeSide.LONG if side == OrderSide.BUY else TradeSide.SHORT
        slippage_rate = (
            self._cost_config.entry_slippage_rate
            if is_entry
            else self._cost_config.exit_slippage_rate
        )
        fee_rate = self._cost_config.entry_fee_rate if is_entry else self._cost_config.exit_fee_rate

        effective_price = calculate_effective_price(
            reference_price=ref_price,
            slippage_rate=slippage_rate,
            side=trade_side,
            is_entry=is_entry,
        )

        dec_qty = Decimal(fill_qty)
        notional = effective_price * dec_qty * self._multiplier
        fee = calculate_fee(notional, fee_rate)
        slippage_loss = abs(effective_price - ref_price) * dec_qty * self._multiplier

        return SimulatedFill(
            order_id=order_id,
            timestamp=candle.exchange_timestamp,
            symbol=symbol,
            side=trade_side,
            intended_qty=intended_qty,
            filled_qty=fill_qty,
            requested_price=ref_price,
            effective_price=effective_price,
            fill_type=FillType.MARKET.value,
            fee=fee,
            slippage_loss=slippage_loss,
            gross_notional=notional,
            reason=f"PARTIAL_FILL_{fill_qty}_OF_{intended_qty}",
        )
