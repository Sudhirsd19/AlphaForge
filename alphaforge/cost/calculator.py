"""
AlphaForge Cost & Slippage Pure Functional Calculator.
Implements pure mathematical formulas for effective execution prices, notionals,
fees, transaction costs, gross/net P&L, and R-multiples.
All formulas strictly enforce fixed-point Decimal arithmetic without floating-point conversion.
"""

from decimal import Decimal

from alphaforge.core.exceptions import CostValidationError
from alphaforge.risk.enums import TradeSide


def calculate_effective_price(
    reference_price: Decimal,
    slippage_rate: Decimal,
    side: TradeSide,
    is_entry: bool,
) -> Decimal:
    """
    Calculate side-aware effective execution price accounting for adverse slippage.

    For LONG:
      - Entry (BUY):  effective_entry = reference_entry * (1 + entry_slippage_rate)
      - Exit (SELL):  effective_exit  = reference_exit  * (1 - exit_slippage_rate)

    For SHORT:
      - Entry (SELL): effective_entry = reference_entry * (1 - entry_slippage_rate)
      - Exit (BUY):   effective_exit  = reference_exit  * (1 + exit_slippage_rate)

    Slippage always acts adversely against the trader.
    Raises CostValidationError if any input is invalid or if effective_price <= 0.
    """
    if not isinstance(reference_price, Decimal) or not reference_price.is_finite():
        raise CostValidationError(f"reference_price must be a finite Decimal: {reference_price}")
    if reference_price <= Decimal("0"):
        raise CostValidationError(f"reference_price must be strictly positive: {reference_price}")

    if not isinstance(slippage_rate, Decimal) or not slippage_rate.is_finite():
        raise CostValidationError(f"slippage_rate must be a finite Decimal: {slippage_rate}")
    if slippage_rate < Decimal("0"):
        raise CostValidationError(f"slippage_rate cannot be negative: {slippage_rate}")

    if side == TradeSide.LONG:
        if is_entry:
            effective_price = reference_price * (Decimal("1") + slippage_rate)
        else:
            effective_price = reference_price * (Decimal("1") - slippage_rate)
    elif side == TradeSide.SHORT:
        if is_entry:
            effective_price = reference_price * (Decimal("1") - slippage_rate)
        else:
            effective_price = reference_price * (Decimal("1") + slippage_rate)
    else:
        raise CostValidationError(f"Unsupported trade side: {side}")

    if not effective_price.is_finite() or effective_price <= Decimal("0"):
        raise CostValidationError(
            f"Effective price must be strictly positive after slippage: {effective_price}"
        )

    return effective_price


def calculate_fee(notional: Decimal, fee_rate: Decimal) -> Decimal:
    """
    Calculate proportional transaction fee on a given notional amount:
    fee = notional * fee_rate
    """
    if not isinstance(notional, Decimal) or not notional.is_finite():
        raise CostValidationError(f"notional must be a finite Decimal: {notional}")
    if notional < Decimal("0"):
        raise CostValidationError(f"notional cannot be negative: {notional}")

    if not isinstance(fee_rate, Decimal) or not fee_rate.is_finite():
        raise CostValidationError(f"fee_rate must be a finite Decimal: {fee_rate}")
    if fee_rate < Decimal("0"):
        raise CostValidationError(f"fee_rate cannot be negative: {fee_rate}")

    return notional * fee_rate


def calculate_transaction_costs(
    entry_notional: Decimal,
    exit_notional: Decimal,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    fixed_cost_per_trade: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Calculate total round-trip transaction costs:
      entry_fee = entry_notional * entry_fee_rate
      exit_fee = exit_notional * exit_fee_rate
      fixed_cost = fixed_cost_per_trade
      transaction_cost = entry_fee + exit_fee + fixed_cost

    Returns (entry_fee, exit_fee, fixed_cost, transaction_cost).
    """
    entry_fee = calculate_fee(entry_notional, entry_fee_rate)
    exit_fee = calculate_fee(exit_notional, exit_fee_rate)

    if not isinstance(fixed_cost_per_trade, Decimal) or not fixed_cost_per_trade.is_finite():
        raise CostValidationError(
            f"fixed_cost_per_trade must be a finite Decimal: {fixed_cost_per_trade}"
        )
    if fixed_cost_per_trade < Decimal("0"):
        raise CostValidationError(
            f"fixed_cost_per_trade cannot be negative: {fixed_cost_per_trade}"
        )

    fixed_cost = fixed_cost_per_trade
    transaction_cost = entry_fee + exit_fee + fixed_cost
    return entry_fee, exit_fee, fixed_cost, transaction_cost


def calculate_gross_pnl(
    effective_entry_price: Decimal,
    effective_exit_price: Decimal,
    quantity: int,
    contract_multiplier: Decimal,
    side: TradeSide,
) -> Decimal:
    """
    Calculate gross monetary P&L based on effective execution prices:
      LONG:  (effective_exit_price - effective_entry_price) * quantity * contract_multiplier
      SHORT: (effective_entry_price - effective_exit_price) * quantity * contract_multiplier
    """
    if not isinstance(effective_entry_price, Decimal) or not effective_entry_price.is_finite():
        raise CostValidationError(
            f"effective_entry_price must be a finite Decimal: {effective_entry_price}"
        )
    if effective_entry_price <= Decimal("0"):
        raise CostValidationError(
            f"effective_entry_price must be strictly positive: {effective_entry_price}"
        )

    if not isinstance(effective_exit_price, Decimal) or not effective_exit_price.is_finite():
        raise CostValidationError(
            f"effective_exit_price must be a finite Decimal: {effective_exit_price}"
        )
    if effective_exit_price <= Decimal("0"):
        raise CostValidationError(
            f"effective_exit_price must be strictly positive: {effective_exit_price}"
        )

    if not isinstance(quantity, int) or quantity <= 0:
        raise CostValidationError(f"quantity must be a strictly positive integer: {quantity}")

    if not isinstance(contract_multiplier, Decimal) or not contract_multiplier.is_finite():
        raise CostValidationError(
            f"contract_multiplier must be a finite Decimal: {contract_multiplier}"
        )
    if contract_multiplier <= Decimal("0"):
        raise CostValidationError(
            f"contract_multiplier must be strictly positive: {contract_multiplier}"
        )

    dec_qty = Decimal(quantity)
    if side == TradeSide.LONG:
        price_diff = effective_exit_price - effective_entry_price
    elif side == TradeSide.SHORT:
        price_diff = effective_entry_price - effective_exit_price
    else:
        raise CostValidationError(f"Unsupported trade side: {side}")

    return price_diff * dec_qty * contract_multiplier


def calculate_net_pnl(gross_pnl: Decimal, transaction_cost: Decimal) -> Decimal:
    """
    Calculate net monetary P&L after deducting round-trip transaction costs:
      net_pnl = gross_pnl - transaction_cost
    """
    if not isinstance(gross_pnl, Decimal) or not gross_pnl.is_finite():
        raise CostValidationError(f"gross_pnl must be a finite Decimal: {gross_pnl}")

    if not isinstance(transaction_cost, Decimal) or not transaction_cost.is_finite():
        raise CostValidationError(f"transaction_cost must be a finite Decimal: {transaction_cost}")
    if transaction_cost < Decimal("0"):
        raise CostValidationError(f"transaction_cost cannot be negative: {transaction_cost}")

    return gross_pnl - transaction_cost


def calculate_r_multiples(
    gross_pnl: Decimal,
    net_pnl: Decimal,
    risk_amount: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Calculate gross and net R-multiples relative to monetary risk denominator:
      gross_R = gross_pnl / risk_amount
      net_R = net_pnl / risk_amount

    risk_amount must be strictly positive.
    """
    if not isinstance(gross_pnl, Decimal) or not gross_pnl.is_finite():
        raise CostValidationError(f"gross_pnl must be a finite Decimal: {gross_pnl}")
    if not isinstance(net_pnl, Decimal) or not net_pnl.is_finite():
        raise CostValidationError(f"net_pnl must be a finite Decimal: {net_pnl}")

    if not isinstance(risk_amount, Decimal) or not risk_amount.is_finite():
        raise CostValidationError(f"risk_amount must be a finite Decimal: {risk_amount}")
    if risk_amount <= Decimal("0"):
        raise CostValidationError(f"risk_amount must be strictly positive: {risk_amount}")

    gross_R = gross_pnl / risk_amount
    net_R = net_pnl / risk_amount
    return gross_R, net_R
