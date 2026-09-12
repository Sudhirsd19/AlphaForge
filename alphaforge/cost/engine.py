"""
AlphaForge Cost & Slippage Engine V1.
Deterministic evaluation engine for transaction costs, execution slippage,
gross/net P&L, and R-multiples.
Fail-closed, strictly Decimal-based, side-aware, and broker-independent.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.core.exceptions import CostValidationError
from alphaforge.cost.calculator import (
    calculate_effective_price,
    calculate_gross_pnl,
    calculate_net_pnl,
    calculate_r_multiples,
    calculate_transaction_costs,
)
from alphaforge.cost.enums import CostDecisionState, CostReasonCode
from alphaforge.cost.models import CostConfig, CostInput, CostResult
from alphaforge.risk.calculator import calculate_notional


def evaluate_trade_cost(
    cost_input: CostInput,
    config: CostConfig | None = None,
) -> CostResult:
    """
    Evaluate expected transaction cost, execution slippage, and net P&L for a trade.

    Fails closed: Any invalid input, non-positive effective price, or calculation anomaly
    produces CostDecisionState.INVALID with a specific CostReasonCode and null numeric fields.

    Calculation flow:
    1. Resolve active CostConfig (passed config > cost_input.cost_config > default CostConfig).
    2. Side-aware effective entry & exit prices via calculate_effective_price.
    3. Notionals via calculate_notional (Phase 5).
    4. Proportional entry/exit fees and fixed cost.
    5. Gross monetary P&L and net monetary P&L.
    6. Gross and net R-multiples relative to risk_amount.
    """
    active_config = (
        config
        if config is not None
        else (cost_input.cost_config if cost_input.cost_config is not None else CostConfig())
    )
    eval_ts = cost_input.timestamp if cost_input.timestamp is not None else datetime.now(UTC)
    calc_version = active_config.calculation_version
    side = cost_input.side

    def _invalid(reason_code: CostReasonCode, reason: str) -> CostResult:
        return CostResult(
            decision=CostDecisionState.INVALID,
            reason_code=reason_code,
            reason=reason,
            side=side,
            calculation_version=calc_version,
            timestamp=eval_ts,
        )

    # 1. Validate numeric inputs
    if (
        not cost_input.entry_reference_price.is_finite()
        or cost_input.entry_reference_price <= Decimal("0")
        or not cost_input.exit_reference_price.is_finite()
        or cost_input.exit_reference_price <= Decimal("0")
    ):
        return _invalid(
            CostReasonCode.INVALID_PRICE,
            "Reference prices must be positive finite Decimals",
        )

    if cost_input.quantity <= 0:
        return _invalid(
            CostReasonCode.INVALID_QUANTITY,
            "Quantity must be a strictly positive integer",
        )

    if not cost_input.contract_multiplier.is_finite() or cost_input.contract_multiplier <= Decimal(
        "0"
    ):
        return _invalid(
            CostReasonCode.INVALID_MULTIPLIER,
            "contract_multiplier must be a positive finite Decimal",
        )

    if not cost_input.risk_amount.is_finite() or cost_input.risk_amount <= Decimal("0"):
        return _invalid(
            CostReasonCode.INVALID_RISK_AMOUNT,
            "risk_amount must be a positive finite Decimal",
        )

    if (
        not active_config.entry_fee_rate.is_finite()
        or active_config.entry_fee_rate < Decimal("0")
        or not active_config.exit_fee_rate.is_finite()
        or active_config.exit_fee_rate < Decimal("0")
    ):
        return _invalid(
            CostReasonCode.INVALID_FEE_RATE,
            "Fee rates must be non-negative finite Decimals",
        )

    if (
        not active_config.entry_slippage_rate.is_finite()
        or active_config.entry_slippage_rate < Decimal("0")
        or not active_config.exit_slippage_rate.is_finite()
        or active_config.exit_slippage_rate < Decimal("0")
    ):
        return _invalid(
            CostReasonCode.INVALID_SLIPPAGE_RATE,
            "Slippage rates must be non-negative finite Decimals",
        )

    if (
        not active_config.fixed_cost_per_trade.is_finite()
        or active_config.fixed_cost_per_trade < Decimal("0")
    ):
        return _invalid(
            CostReasonCode.INVALID_FIXED_COST,
            "fixed_cost_per_trade must be a non-negative finite Decimal",
        )

    # 2. Side-aware effective execution prices
    try:
        effective_entry = calculate_effective_price(
            reference_price=cost_input.entry_reference_price,
            slippage_rate=active_config.entry_slippage_rate,
            side=side,
            is_entry=True,
        )
        effective_exit = calculate_effective_price(
            reference_price=cost_input.exit_reference_price,
            slippage_rate=active_config.exit_slippage_rate,
            side=side,
            is_entry=False,
        )
    except CostValidationError as err:
        return _invalid(
            CostReasonCode.EFFECTIVE_PRICE_NON_POSITIVE,
            f"Effective price validation failed: {err}",
        )

    if effective_entry <= Decimal("0") or effective_exit <= Decimal("0"):
        return _invalid(
            CostReasonCode.EFFECTIVE_PRICE_NON_POSITIVE,
            "Effective execution price must be strictly positive after slippage",
        )

    # 3. Notional calculations (Phase 5 calculate_notional)
    try:
        entry_notional = calculate_notional(
            price=effective_entry,
            quantity=cost_input.quantity,
            contract_multiplier=cost_input.contract_multiplier,
        )
        exit_notional = calculate_notional(
            price=effective_exit,
            quantity=cost_input.quantity,
            contract_multiplier=cost_input.contract_multiplier,
        )
    except Exception as err:
        return _invalid(CostReasonCode.UNKNOWN_COST_STATE, f"Notional calculation failed: {err}")

    # 4. Proportional fees & fixed friction
    try:
        entry_fee, exit_fee, fixed_cost, transaction_cost = calculate_transaction_costs(
            entry_notional=entry_notional,
            exit_notional=exit_notional,
            entry_fee_rate=active_config.entry_fee_rate,
            exit_fee_rate=active_config.exit_fee_rate,
            fixed_cost_per_trade=active_config.fixed_cost_per_trade,
        )
    except CostValidationError as err:
        return _invalid(
            CostReasonCode.UNKNOWN_COST_STATE,
            f"Transaction cost calculation failed: {err}",
        )

    # 5. Gross and net P&L
    try:
        gross_pnl = calculate_gross_pnl(
            effective_entry_price=effective_entry,
            effective_exit_price=effective_exit,
            quantity=cost_input.quantity,
            contract_multiplier=cost_input.contract_multiplier,
            side=side,
        )
        net_pnl = calculate_net_pnl(gross_pnl=gross_pnl, transaction_cost=transaction_cost)
    except CostValidationError as err:
        return _invalid(CostReasonCode.UNKNOWN_COST_STATE, f"P&L calculation failed: {err}")

    # 6. R-Multiples
    try:
        gross_R, net_R = calculate_r_multiples(
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            risk_amount=cost_input.risk_amount,
        )
    except CostValidationError as err:
        return _invalid(CostReasonCode.UNKNOWN_COST_STATE, f"R-multiple calculation failed: {err}")

    return CostResult(
        decision=CostDecisionState.VALID,
        reason_code=CostReasonCode.VALID,
        reason="Cost and slippage evaluation successful.",
        side=side,
        reference_entry_price=cost_input.entry_reference_price,
        effective_entry_price=effective_entry,
        reference_exit_price=cost_input.exit_reference_price,
        effective_exit_price=effective_exit,
        quantity=cost_input.quantity,
        contract_multiplier=cost_input.contract_multiplier,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        fixed_cost=fixed_cost,
        transaction_cost=transaction_cost,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        risk_amount=cost_input.risk_amount,
        gross_R=gross_R,
        net_R=net_R,
        calculation_version=calc_version,
        timestamp=eval_ts,
    )


class CostEngine:
    """
    Deterministic Cost & Slippage Engine instance.
    Evaluates proposed trades against configured transaction friction and slippage parameters.
    """

    def __init__(self, config: CostConfig | None = None) -> None:
        self._config = config if config is not None else CostConfig()

    @property
    def config(self) -> CostConfig:
        return self._config

    def evaluate(self, cost_input: CostInput) -> CostResult:
        """Evaluate trade cost using engine's configured parameters."""
        return evaluate_trade_cost(cost_input, config=self._config)
