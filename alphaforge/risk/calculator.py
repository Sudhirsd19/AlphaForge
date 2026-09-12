"""
AlphaForge Risk Engine Pure Functional Calculator.
Implements pure mathematical formulas for risk distances, sizing, notionals, and daily losses.
All formulas strictly enforce fixed-point Decimal arithmetic without floating point conversion.
"""

from decimal import Decimal

from alphaforge.core.exceptions import RiskValidationError


def calculate_risk_distance(entry_price: Decimal, stop_price: Decimal) -> Decimal:
    """
    Calculate absolute stop loss distance in price points:
    risk_distance = abs(entry_price - stop_price)
    """
    if not entry_price.is_finite() or not stop_price.is_finite():
        raise RiskValidationError("Prices must be finite Decimals")
    if entry_price <= Decimal("0") or stop_price <= Decimal("0"):
        raise RiskValidationError("Prices must be strictly positive")

    distance = abs(entry_price - stop_price)
    if distance <= Decimal("0"):
        raise RiskValidationError("Risk distance must be strictly positive")
    return distance


def calculate_stop_distance_pct(entry_price: Decimal, stop_price: Decimal) -> Decimal:
    """
    Calculate stop distance as a normalized fraction of entry price:
    stop_distance_pct = abs(entry_price - stop_price) / entry_price
    """
    distance = calculate_risk_distance(entry_price, stop_price)
    return distance / entry_price


def calculate_max_trade_risk(account_equity: Decimal, max_risk_per_trade: Decimal) -> Decimal:
    """
    Calculate maximum permissible monetary risk budget for a single trade:
    max_trade_risk = account_equity * max_risk_per_trade
    """
    if not account_equity.is_finite() or account_equity <= Decimal("0"):
        raise RiskValidationError("account_equity must be a positive finite Decimal")
    if not max_risk_per_trade.is_finite() or max_risk_per_trade <= Decimal("0"):
        raise RiskValidationError("max_risk_per_trade must be a positive finite Decimal")

    return account_equity * max_risk_per_trade


def calculate_risk_per_unit(risk_distance: Decimal, contract_multiplier: Decimal) -> Decimal:
    """
    Calculate monetary risk per single unit of instrument quantity:
    risk_per_unit = risk_distance * contract_multiplier
    """
    if not risk_distance.is_finite() or risk_distance <= Decimal("0"):
        raise RiskValidationError("risk_distance must be a positive finite Decimal")
    if not contract_multiplier.is_finite() or contract_multiplier <= Decimal("0"):
        raise RiskValidationError("contract_multiplier must be a positive finite Decimal")

    return risk_distance * contract_multiplier


def calculate_position_size(
    max_trade_risk: Decimal,
    risk_per_unit: Decimal,
    lot_size: int,
    allow_lot_floor: bool = False,
) -> int:
    """
    Determine trade quantity in units adhering to lot-size rules:
    raw_quantity = max_trade_risk / risk_per_unit

    quantity MUST satisfy:
      quantity > 0
      quantity % lot_size == 0

    If allow_lot_floor is False:
      Never silently round or floor quantity. If exact multiple cannot be established: REJECT.
    """
    if not max_trade_risk.is_finite() or max_trade_risk <= Decimal("0"):
        raise RiskValidationError("max_trade_risk must be a positive finite Decimal")
    if not risk_per_unit.is_finite() or risk_per_unit <= Decimal("0"):
        raise RiskValidationError("risk_per_unit must be a positive finite Decimal")
    if lot_size <= 0:
        raise RiskValidationError("lot_size must be a strictly positive integer")

    raw_quantity = max_trade_risk / risk_per_unit
    dec_lot = Decimal(lot_size)

    if raw_quantity < dec_lot:
        raise RiskValidationError(
            f"Calculated raw_quantity ({raw_quantity}) is smaller than lot_size ({lot_size})"
        )

    # Check if raw_quantity is an exact integer multiple of lot_size
    remainder = raw_quantity % dec_lot
    if remainder == Decimal("0") and raw_quantity == Decimal(int(raw_quantity)):
        return int(raw_quantity)

    if not allow_lot_floor:
        raise RiskValidationError(
            f"Calculated raw_quantity ({raw_quantity}) is not an exact multiple of "
            f"lot_size ({lot_size}). Silent rounding/flooring is forbidden."
        )

    # Explicitly permitted flooring to nearest lower lot
    floored_lots = int(raw_quantity // dec_lot)
    return floored_lots * lot_size


def calculate_notional(
    price: Decimal,
    quantity: int,
    contract_multiplier: Decimal = Decimal("1"),
) -> Decimal:
    """
    Calculate total notional exposure value of a position:
    notional = price * quantity * contract_multiplier
    """
    if not price.is_finite() or price <= Decimal("0"):
        raise RiskValidationError("price must be a positive finite Decimal")
    if quantity <= 0:
        raise RiskValidationError("quantity must be a positive integer")
    if not contract_multiplier.is_finite() or contract_multiplier <= Decimal("0"):
        raise RiskValidationError("contract_multiplier must be a positive finite Decimal")

    return price * Decimal(quantity) * contract_multiplier


def calculate_daily_loss(
    daily_starting_equity: Decimal,
    current_equity: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Calculate daily loss and percentage loss relative to starting equity:
    daily_loss = max(0, daily_starting_equity - current_equity)
    daily_loss_pct = daily_loss / daily_starting_equity
    """
    if not daily_starting_equity.is_finite() or daily_starting_equity <= Decimal("0"):
        raise RiskValidationError("daily_starting_equity must be a positive finite Decimal")
    if not current_equity.is_finite():
        raise RiskValidationError("current_equity must be a finite Decimal")

    loss = max(Decimal("0"), daily_starting_equity - current_equity)
    loss_pct = loss / daily_starting_equity
    return loss, loss_pct


def calculate_required_capital(
    monetary_risk: Decimal,
    reserve_buffer: Decimal = Decimal("0.05"),
) -> Decimal:
    """
    Calculate required capital/margin including risk reserve buffer:
    required_capital = monetary_risk * (1 + reserve_buffer)
    """
    if not monetary_risk.is_finite() or monetary_risk <= Decimal("0"):
        raise RiskValidationError("monetary_risk must be a positive finite Decimal")
    if not reserve_buffer.is_finite() or reserve_buffer < Decimal("0"):
        raise RiskValidationError("reserve_buffer must be a non-negative finite Decimal")

    return monetary_risk * (Decimal("1") + reserve_buffer)
