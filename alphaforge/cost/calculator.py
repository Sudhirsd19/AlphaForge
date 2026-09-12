"""Pure deterministic Phase 6 cost and slippage calculator."""

from decimal import Decimal

from alphaforge.cost.enums import CostReasonCode
from alphaforge.cost.exceptions import CostValidationError
from alphaforge.cost.models import CostInput, CostResult
from alphaforge.risk.enums import TradeSide

CALCULATION_VERSION = "PHASE6_COST_V1"
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _validate_cost_input(cost_input: CostInput) -> None:
    for name in (
        "entry_reference_price",
        "exit_reference_price",
        "contract_multiplier",
        "risk_amount",
    ):
        value = getattr(cost_input, name)
        if not value.is_finite() or value <= _ZERO:
            raise CostValidationError(
                f"{name} must be a positive finite Decimal",
                CostReasonCode.INVALID_INPUT,
            )
    if cost_input.quantity <= 0:
        raise CostValidationError(
            "quantity must be strictly positive",
            CostReasonCode.INVALID_INPUT,
        )


def _effective_prices(cost_input: CostInput) -> tuple[Decimal, Decimal]:
    cfg = cost_input.cost_config
    if cost_input.side is TradeSide.LONG:
        effective_entry = cost_input.entry_reference_price * (_ONE + cfg.entry_slippage_rate)
        effective_exit = cost_input.exit_reference_price * (_ONE - cfg.exit_slippage_rate)
    else:
        effective_entry = cost_input.entry_reference_price * (_ONE - cfg.entry_slippage_rate)
        effective_exit = cost_input.exit_reference_price * (_ONE + cfg.exit_slippage_rate)

    if not effective_entry.is_finite() or not effective_exit.is_finite():
        raise CostValidationError(
            "effective execution price must be finite",
            CostReasonCode.INVALID_EFFECTIVE_PRICE,
        )
    if effective_entry <= _ZERO or effective_exit <= _ZERO:
        raise CostValidationError(
            "effective execution price must be strictly positive",
            CostReasonCode.INVALID_EFFECTIVE_PRICE,
        )
    return effective_entry, effective_exit


def calculate_cost(cost_input: CostInput) -> CostResult:
    """Calculate slippage, transaction costs, gross P&L, and net P&L.

    No market data, broker API, order routing, or wall-clock default is used.
    All monetary arithmetic remains Decimal-only and no intermediate rounding is applied.
    """
    _validate_cost_input(cost_input)
    effective_entry, effective_exit = _effective_prices(cost_input)

    quantity = Decimal(cost_input.quantity)
    multiplier = cost_input.contract_multiplier
    cfg = cost_input.cost_config

    entry_notional = effective_entry * quantity * multiplier
    exit_notional = effective_exit * quantity * multiplier

    entry_fee = entry_notional * cfg.entry_fee_rate
    exit_fee = exit_notional * cfg.exit_fee_rate
    transaction_cost = entry_fee + exit_fee + cfg.fixed_cost_per_trade

    entry_slippage_cost = (
        abs(effective_entry - cost_input.entry_reference_price) * quantity * multiplier
    )
    exit_slippage_cost = (
        abs(effective_exit - cost_input.exit_reference_price) * quantity * multiplier
    )
    total_slippage_cost = entry_slippage_cost + exit_slippage_cost

    if cost_input.side is TradeSide.LONG:
        gross_pnl = (effective_exit - effective_entry) * quantity * multiplier
    else:
        gross_pnl = (effective_entry - effective_exit) * quantity * multiplier

    net_pnl = gross_pnl - transaction_cost
    gross_R = gross_pnl / cost_input.risk_amount
    net_R = net_pnl / cost_input.risk_amount
    total_round_trip_cost = total_slippage_cost + transaction_cost

    monetary_values = (
        entry_notional,
        exit_notional,
        entry_slippage_cost,
        exit_slippage_cost,
        total_slippage_cost,
        entry_fee,
        exit_fee,
        transaction_cost,
        total_round_trip_cost,
        gross_pnl,
        net_pnl,
        gross_R,
        net_R,
    )
    if any(not value.is_finite() for value in monetary_values):
        raise CostValidationError(
            "cost calculation produced a non-finite Decimal",
            CostReasonCode.NON_FINITE_CALCULATION,
        )
    if transaction_cost < _ZERO or total_round_trip_cost < _ZERO:
        raise CostValidationError(
            "transaction costs must never be negative",
            CostReasonCode.NON_FINITE_CALCULATION,
        )

    return CostResult(
        side=cost_input.side,
        reference_entry_price=cost_input.entry_reference_price,
        effective_entry_price=effective_entry,
        reference_exit_price=cost_input.exit_reference_price,
        effective_exit_price=effective_exit,
        quantity=cost_input.quantity,
        contract_multiplier=multiplier,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        entry_slippage_cost=entry_slippage_cost,
        exit_slippage_cost=exit_slippage_cost,
        total_slippage_cost=total_slippage_cost,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        fixed_cost=cfg.fixed_cost_per_trade,
        transaction_cost=transaction_cost,
        total_round_trip_cost=total_round_trip_cost,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        risk_amount=cost_input.risk_amount,
        gross_R=gross_R,
        net_R=net_R,
        calculation_version=CALCULATION_VERSION,
        timestamp=cost_input.timestamp,
        symbol=cost_input.symbol,
        signal_id=cost_input.signal_id,
    )
