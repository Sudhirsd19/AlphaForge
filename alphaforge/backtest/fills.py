"""
AlphaForge Simulated Fill Engine.
Implements deterministic offline execution simulation for market, limit,
and resting protection orders (stop-loss and take-profit).
Enforces Phase 6 cost/slippage integration and conservative same-bar SL/TP resolution.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.core.exceptions import BacktestValidationError, ImpossibleFillError
from alphaforge.cost.calculator import calculate_effective_price, calculate_fee
from alphaforge.cost.models import CostConfig
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


class SimulatedFill(BaseModel):
    """
    Immutable record of a simulated order execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: str = Field(description="Deterministic client order identifier")
    timestamp: datetime = Field(description="Execution fill timestamp in UTC")
    symbol: str = Field(description="Market symbol")
    side: TradeSide = Field(description="LONG or SHORT")
    intended_qty: int = Field(gt=0, description="Intended quantity from order intent")
    filled_qty: int = Field(gt=0, description="Actually executed quantity")
    requested_price: Decimal = Field(
        gt=Decimal("0"), description="Reference benchmark price before slippage"
    )
    effective_price: Decimal = Field(
        gt=Decimal("0"), description="Actual fill price after adverse slippage"
    )
    fill_type: str = Field(description="MARKET, LIMIT, STOP_LOSS, TAKE_PROFIT, FORCED_CLOSE")
    fee: Decimal = Field(ge=Decimal("0"), description="Transaction fee incurred")
    slippage_loss: Decimal = Field(ge=Decimal("0"), description="Monetary slippage loss incurred")
    gross_notional: Decimal = Field(
        gt=Decimal("0"), description="Effective notional value (price * qty * multiplier)"
    )
    reason: str = Field(description="Execution trigger or justification")

    @field_validator("timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC: {v}")
        return v


class SimulatedFillEngine:
    """
    Pure deterministic execution simulator consuming Phase 6 cost and slippage models.
    """

    @staticmethod
    def simulate_entry(
        order_id: str,
        symbol: str,
        side: TradeSide,
        quantity: int,
        candle: MarketCandle,
        cost_config: CostConfig,
        multiplier: Decimal = Decimal("1"),
    ) -> SimulatedFill:
        """
        Simulate immediate market entry on candle open (earliest ordinary execution at T+1).
        Applies adverse slippage and transaction fee.
        """
        if quantity <= 0:
            raise BacktestValidationError(f"Invalid quantity: {quantity}")
        ref_price = candle.open
        if ref_price <= Decimal("0"):
            raise ImpossibleFillError(f"Cannot fill at non-positive price: {ref_price}")

        effective_price = calculate_effective_price(
            reference_price=ref_price,
            slippage_rate=cost_config.entry_slippage_rate,
            side=side,
            is_entry=True,
        )

        dec_qty = Decimal(quantity)
        notional = effective_price * dec_qty * multiplier
        fee = calculate_fee(notional, cost_config.entry_fee_rate)
        slippage_loss = abs(effective_price - ref_price) * dec_qty * multiplier

        return SimulatedFill(
            order_id=order_id,
            timestamp=candle.exchange_timestamp,
            symbol=symbol,
            side=side,
            intended_qty=quantity,
            filled_qty=quantity,
            requested_price=ref_price,
            effective_price=effective_price,
            fill_type="MARKET",
            fee=fee,
            slippage_loss=slippage_loss,
            gross_notional=notional,
            reason="ENTRY_MARKET_OPEN",
        )

    @staticmethod
    def evaluate_resting_brackets(
        order_id: str,
        symbol: str,
        side: TradeSide,
        quantity: int,
        stop_price: Decimal,
        target_price: Decimal,
        candle: MarketCandle,
        cost_config: CostConfig,
        conservative_same_bar_sl_first: bool = True,
        multiplier: Decimal = Decimal("1"),
    ) -> tuple[bool, SimulatedFill | None, str | None]:
        """
        Evaluate resting stop-loss and take-profit protection against candle range.
        Section 15: If both SL and TP are touched on the same bar, conservatively trigger SL first!
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
            return False, None, None

        # Determine which executed based on conservative policy
        execute_sl = False
        if sl_hit and tp_hit:
            # Ambiguity: conservative policy enforces stop loss first
            execute_sl = conservative_same_bar_sl_first
        elif sl_hit:
            execute_sl = True
        else:
            execute_sl = False

        if execute_sl:
            # Stop Loss fill with realistic gap handling
            if side == TradeSide.LONG:
                ref_exit = open_p if open_p < stop_price else stop_price
            else:
                ref_exit = open_p if open_p > stop_price else stop_price

            effective_exit = calculate_effective_price(
                reference_price=ref_exit,
                slippage_rate=cost_config.exit_slippage_rate,
                side=side,
                is_entry=False,
            )
            notional = effective_exit * dec_qty * multiplier
            fee = (
                calculate_fee(notional, cost_config.exit_fee_rate)
                + cost_config.fixed_cost_per_trade
            )
            slippage_loss = abs(effective_exit - ref_exit) * dec_qty * multiplier

            fill = SimulatedFill(
                order_id=order_id,
                timestamp=candle.exchange_timestamp,
                symbol=symbol,
                side=side,
                intended_qty=quantity,
                filled_qty=quantity,
                requested_price=ref_exit,
                effective_price=effective_exit,
                fill_type="STOP_LOSS",
                fee=fee,
                slippage_loss=slippage_loss,
                gross_notional=notional,
                reason="RESTING_STOP_LOSS_TRIGGERED",
            )
            return True, fill, "STOP_LOSS"

        else:
            # Take Profit fill
            ref_exit = target_price
            effective_exit = calculate_effective_price(
                reference_price=ref_exit,
                slippage_rate=cost_config.exit_slippage_rate,
                side=side,
                is_entry=False,
            )
            notional = effective_exit * dec_qty * multiplier
            fee = (
                calculate_fee(notional, cost_config.exit_fee_rate)
                + cost_config.fixed_cost_per_trade
            )
            slippage_loss = abs(effective_exit - ref_exit) * dec_qty * multiplier

            fill = SimulatedFill(
                order_id=order_id,
                timestamp=candle.exchange_timestamp,
                symbol=symbol,
                side=side,
                intended_qty=quantity,
                filled_qty=quantity,
                requested_price=ref_exit,
                effective_price=effective_exit,
                fill_type="TAKE_PROFIT",
                fee=fee,
                slippage_loss=slippage_loss,
                gross_notional=notional,
                reason="RESTING_TAKE_PROFIT_TRIGGERED",
            )
            return True, fill, "TARGET"

    @staticmethod
    def simulate_forced_close(
        order_id: str,
        symbol: str,
        side: TradeSide,
        quantity: int,
        candle: MarketCandle,
        cost_config: CostConfig,
        multiplier: Decimal = Decimal("1"),
    ) -> SimulatedFill:
        """
        Force-close position at final bar close under end-of-test forced closure policy.
        """
        ref_price = candle.close
        effective_price = calculate_effective_price(
            reference_price=ref_price,
            slippage_rate=cost_config.exit_slippage_rate,
            side=side,
            is_entry=False,
        )
        dec_qty = Decimal(quantity)
        notional = effective_price * dec_qty * multiplier
        fee = calculate_fee(notional, cost_config.exit_fee_rate) + cost_config.fixed_cost_per_trade
        slippage_loss = abs(effective_price - ref_price) * dec_qty * multiplier

        return SimulatedFill(
            order_id=order_id,
            timestamp=candle.exchange_timestamp,
            symbol=symbol,
            side=side,
            intended_qty=quantity,
            filled_qty=quantity,
            requested_price=ref_price,
            effective_price=effective_price,
            fill_type="FORCED_CLOSE",
            fee=fee,
            slippage_loss=slippage_loss,
            gross_notional=notional,
            reason="END_OF_TEST_FORCED_CLOSE",
        )
