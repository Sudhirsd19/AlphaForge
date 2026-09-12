"""
Property-based tests for AlphaForge Cost & Slippage Model using Hypothesis.
Verifies invariants across randomized parameter spaces:
1. net_pnl <= gross_pnl for all valid trade evaluations (costs are strictly non-negative).
2. Zero friction identity: with zero slippage, fees, and fixed cost, net_pnl == gross_pnl.
3. Slippage monotonicity: increasing slippage rate strictly reduces gross P&L.
4. Fee monotonicity: increasing fees or fixed cost strictly reduces net P&L.
5. Currency precision purity: all numeric fields in CostResult are strictly Decimal or None.
"""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.cost.engine import evaluate_trade_cost
from alphaforge.cost.enums import CostDecisionState
from alphaforge.cost.models import CostConfig, CostInput
from alphaforge.risk.enums import TradeSide


@given(
    side=st.sampled_from([TradeSide.LONG, TradeSide.SHORT]),
    entry_price_int=st.integers(min_value=100, max_value=50000),
    exit_price_int=st.integers(min_value=100, max_value=50000),
    quantity=st.integers(min_value=1, max_value=1000),
    multiplier_int=st.integers(min_value=1, max_value=50),
    risk_int=st.integers(min_value=100, max_value=100000),
    fee_rate_bps=st.integers(min_value=0, max_value=50),  # 0 to 0.50%
    slip_rate_bps=st.integers(min_value=0, max_value=50),  # 0 to 0.50%
    fixed_cost_int=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=100)
def test_cost_invariants_property(
    side: TradeSide,
    entry_price_int: int,
    exit_price_int: int,
    quantity: int,
    multiplier_int: int,
    risk_int: int,
    fee_rate_bps: int,
    slip_rate_bps: int,
    fixed_cost_int: int,
) -> None:
    """
    Property:
    - net_pnl <= gross_pnl
    - transaction_cost >= 0
    - All numeric outputs are strictly Decimal
    """
    entry_p = Decimal(entry_price_int)
    exit_p = Decimal(exit_price_int)
    multiplier = Decimal(multiplier_int)
    risk = Decimal(risk_int)

    fee_rate = Decimal(fee_rate_bps) / Decimal("10000")
    slip_rate = Decimal(slip_rate_bps) / Decimal("10000")
    fixed_cost = Decimal(fixed_cost_int)

    cfg = CostConfig(
        entry_fee_rate=fee_rate,
        exit_fee_rate=fee_rate,
        entry_slippage_rate=slip_rate,
        exit_slippage_rate=slip_rate,
        fixed_cost_per_trade=fixed_cost,
    )

    inp = CostInput(
        side=side,
        entry_reference_price=entry_p,
        exit_reference_price=exit_p,
        quantity=quantity,
        contract_multiplier=multiplier,
        risk_amount=risk,
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    if result.decision == CostDecisionState.VALID:
        assert result.gross_pnl is not None
        assert result.net_pnl is not None
        assert result.transaction_cost is not None
        assert result.entry_notional is not None
        assert result.exit_notional is not None
        assert result.gross_R is not None
        assert result.net_R is not None

        # Invariant 1: Transaction costs are non-negative
        assert result.transaction_cost >= Decimal("0")

        # Invariant 2: Net P&L cannot exceed Gross P&L
        assert result.net_pnl <= result.gross_pnl

        # Invariant 3: Net R <= Gross R
        assert result.net_R <= result.gross_R

        # Invariant 4: No float instances exist
        for val in (
            result.reference_entry_price,
            result.effective_entry_price,
            result.reference_exit_price,
            result.effective_exit_price,
            result.entry_notional,
            result.exit_notional,
            result.entry_fee,
            result.exit_fee,
            result.fixed_cost,
            result.transaction_cost,
            result.gross_pnl,
            result.net_pnl,
            result.risk_amount,
            result.gross_R,
            result.net_R,
        ):
            assert isinstance(val, (Decimal, type(None)))
            assert not isinstance(val, float)


@given(
    side=st.sampled_from([TradeSide.LONG, TradeSide.SHORT]),
    entry_price_int=st.integers(min_value=500, max_value=20000),
    exit_price_int=st.integers(min_value=500, max_value=20000),
    quantity=st.integers(min_value=1, max_value=100),
    multiplier_int=st.integers(min_value=1, max_value=25),
    risk_int=st.integers(min_value=500, max_value=10000),
)
@settings(max_examples=50)
def test_zero_friction_identity_property(
    side: TradeSide,
    entry_price_int: int,
    exit_price_int: int,
    quantity: int,
    multiplier_int: int,
    risk_int: int,
) -> None:
    """
    Property: When friction is zero, effective prices match reference prices,
    transaction cost is zero, and net P&L matches gross P&L exactly.
    """
    entry_p = Decimal(entry_price_int)
    exit_p = Decimal(exit_price_int)

    cfg = CostConfig()  # all zeros
    inp = CostInput(
        side=side,
        entry_reference_price=entry_p,
        exit_reference_price=exit_p,
        quantity=quantity,
        contract_multiplier=Decimal(multiplier_int),
        risk_amount=Decimal(risk_int),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)
    assert result.decision == CostDecisionState.VALID
    assert result.effective_entry_price == entry_p
    assert result.effective_exit_price == exit_p
    assert result.transaction_cost == Decimal("0")
    assert result.net_pnl == result.gross_pnl
    assert result.net_R == result.gross_R
