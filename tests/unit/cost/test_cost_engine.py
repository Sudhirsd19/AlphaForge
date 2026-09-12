"""
Unit tests for AlphaForge Cost & Slippage Engine (evaluate_trade_cost and CostEngine).
Covers scenarios A–AE:
- Zero friction baseline
- Slippage on entry and exit (LONG and SHORT)
- Proportional fees (entry and exit)
- Fixed friction per trade
- Combined friction (slippage, fees, fixed cost)
- Profitable trade turned negative by transaction friction
- Contract multiplier scaling
- R-multiple calculations (gross and net)
- Fail-closed handling for non-positive effective prices
- Null fields on rejection
- Bit-for-bit determinism
- CostEngine class wrapper consistency
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.cost.engine import CostEngine, evaluate_trade_cost
from alphaforge.cost.enums import CostDecisionState, CostReasonCode
from alphaforge.cost.models import CostConfig, CostInput
from alphaforge.risk.enums import TradeSide


def test_zero_friction_baseline_long() -> None:
    """Zero fees, slippage, and fixed cost: effective prices equal reference, gross equals net."""
    cfg = CostConfig()  # all zeros
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("110.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("50.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    assert result.decision == CostDecisionState.VALID
    assert result.reason_code == CostReasonCode.VALID
    assert result.effective_entry_price == Decimal("100.00")
    assert result.effective_exit_price == Decimal("110.00")
    assert result.entry_notional == Decimal("1000.00")
    assert result.exit_notional == Decimal("1100.00")
    assert result.entry_fee == Decimal("0")
    assert result.exit_fee == Decimal("0")
    assert result.fixed_cost == Decimal("0")
    assert result.transaction_cost == Decimal("0")
    assert result.gross_pnl == Decimal("100.00")
    assert result.net_pnl == Decimal("100.00")
    assert result.gross_R == Decimal("2.0")
    assert result.net_R == Decimal("2.0")


def test_zero_friction_baseline_short() -> None:
    """SHORT zero friction: entry 100, exit 90 -> gross = +10 * 10 = +100."""
    cfg = CostConfig()
    inp = CostInput(
        side=TradeSide.SHORT,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("90.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("50.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    assert result.decision == CostDecisionState.VALID
    assert result.effective_entry_price == Decimal("100.00")
    assert result.effective_exit_price == Decimal("90.00")
    assert result.transaction_cost == Decimal("0")
    assert result.gross_pnl == Decimal("100.00")
    assert result.net_pnl == Decimal("100.00")


def test_long_slippage_only() -> None:
    """LONG with slippage: entry pays higher, exit receives lower."""
    cfg = CostConfig(
        entry_slippage_rate=Decimal("0.0010"),  # 0.10% -> 100 * 1.001 = 100.10
        exit_slippage_rate=Decimal("0.0010"),  # 0.10% -> 110 * 0.999 = 109.89
    )
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("110.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("50.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    assert result.effective_entry_price == Decimal("100.1000")
    assert result.effective_exit_price == Decimal("109.8900")
    # Gross P&L: (109.89 - 100.10) * 10 = 9.79 * 10 = 97.90
    assert result.gross_pnl == Decimal("97.9000")
    assert result.transaction_cost == Decimal("0")
    assert result.net_pnl == Decimal("97.9000")


def test_short_slippage_only() -> None:
    """SHORT with slippage: entry sells lower, exit buys higher."""
    cfg = CostConfig(
        entry_slippage_rate=Decimal("0.0010"),  # 0.10% -> 100 * 0.999 = 99.90
        exit_slippage_rate=Decimal("0.0010"),  # 0.10% -> 90 * 1.001 = 90.09
    )
    inp = CostInput(
        side=TradeSide.SHORT,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("90.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("50.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    assert result.effective_entry_price == Decimal("99.9000")
    assert result.effective_exit_price == Decimal("90.0900")
    # Gross P&L: (99.90 - 90.09) * 10 = 9.81 * 10 = 98.10
    assert result.gross_pnl == Decimal("98.1000")
    assert result.net_pnl == Decimal("98.1000")


def test_fees_and_fixed_cost() -> None:
    """Proportional entry/exit fees and fixed cost per trade."""
    cfg = CostConfig(
        entry_fee_rate=Decimal("0.0002"),  # 0.02%
        exit_fee_rate=Decimal("0.0003"),  # 0.03%
        fixed_cost_per_trade=Decimal("20.00"),
    )
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("1000.00"),
        exit_reference_price=Decimal("1050.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("200.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    # entry_notional = 1000 * 10 = 10,000.00
    # exit_notional = 1050 * 10 = 10,500.00
    # entry_fee = 10,000 * 0.0002 = 2.00
    # exit_fee = 10,500 * 0.0003 = 3.15
    # fixed_cost = 20.00
    # transaction_cost = 2.00 + 3.15 + 20.00 = 25.15
    assert result.entry_fee == Decimal("2.000000")
    assert result.exit_fee == Decimal("3.150000")
    assert result.fixed_cost == Decimal("20.00")
    assert result.transaction_cost == Decimal("25.150000")

    # gross_pnl = (1050 - 1000) * 10 = 500.00
    assert result.gross_pnl == Decimal("500.00")
    # net_pnl = 500.00 - 25.15 = 474.85
    assert result.net_pnl == Decimal("474.850000")


def test_profitable_gross_turned_negative_by_friction() -> None:
    """Small gross profit becomes net loss after deducting transaction friction."""
    cfg = CostConfig(
        fixed_cost_per_trade=Decimal("100.00"),
    )
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("105.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("100.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    # gross_pnl = +50.00, friction = 100.00 -> net_pnl = -50.00
    assert result.gross_pnl == Decimal("50.00")
    assert result.net_pnl == Decimal("-50.00")
    assert result.gross_R == Decimal("0.5")
    assert result.net_R == Decimal("-0.5")


def test_contract_multiplier_scaling() -> None:
    """Contract multiplier scales notional, gross P&L, and proportional fees."""
    cfg = CostConfig(
        entry_fee_rate=Decimal("0.001"),
    )
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("24000.00"),
        exit_reference_price=Decimal("24100.00"),
        quantity=2,
        contract_multiplier=Decimal("25"),  # NIFTY lot scaling
        risk_amount=Decimal("5000.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    # entry_notional = 24000 * 2 * 25 = 1,200,000.00
    assert result.entry_notional == Decimal("1200000.00")
    # entry_fee = 1,200,000 * 0.001 = 1200.00
    assert result.entry_fee == Decimal("1200.0000")
    # gross_pnl = 100 * 2 * 25 = 5000.00
    assert result.gross_pnl == Decimal("5000.00")


def test_effective_price_non_positive_fails_closed() -> None:
    """Excessive slippage driving effective price <= 0 results in INVALID and null fields."""
    # 100% exit slippage on LONG drives exit price to 0
    cfg = CostConfig(exit_slippage_rate=Decimal("1.0"))
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("100.00"),
        exit_reference_price=Decimal("100.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("100.00"),
        timestamp=datetime.now(UTC),
    )

    result = evaluate_trade_cost(inp, config=cfg)

    assert result.decision == CostDecisionState.INVALID
    assert result.reason_code == CostReasonCode.EFFECTIVE_PRICE_NON_POSITIVE
    assert result.effective_entry_price is None
    assert result.effective_exit_price is None
    assert result.gross_pnl is None
    assert result.net_pnl is None
    assert result.transaction_cost is None


def test_determinism_identical_results() -> None:
    """Repeating the same cost evaluation yields bit-for-bit identical outputs."""
    ts = datetime.now(UTC)
    cfg = CostConfig(
        entry_fee_rate=Decimal("0.0005"),
        entry_slippage_rate=Decimal("0.0002"),
    )
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("1000.00"),
        exit_reference_price=Decimal("1020.00"),
        quantity=5,
        contract_multiplier=Decimal("10"),
        risk_amount=Decimal("1000.00"),
        timestamp=ts,
    )

    res1 = evaluate_trade_cost(inp, config=cfg)
    res2 = evaluate_trade_cost(inp, config=cfg)

    assert res1.model_dump() == res2.model_dump()


def test_cost_engine_class_wrapper_consistency() -> None:
    """CostEngine.evaluate() produces identical result to evaluate_trade_cost()."""
    cfg = CostConfig(
        entry_fee_rate=Decimal("0.0005"),
        fixed_cost_per_trade=Decimal("15.00"),
    )
    engine = CostEngine(config=cfg)
    inp = CostInput(
        side=TradeSide.SHORT,
        entry_reference_price=Decimal("500.00"),
        exit_reference_price=Decimal("490.00"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("200.00"),
        timestamp=datetime.now(UTC),
    )

    res_engine = engine.evaluate(inp)
    res_direct = evaluate_trade_cost(inp, config=cfg)

    assert res_engine.model_dump() == res_direct.model_dump()
