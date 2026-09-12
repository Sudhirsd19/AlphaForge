"""
Unit tests for AlphaForge Cost & Slippage pure functional calculator.
Verifies formulas for side-aware effective execution prices, notionals,
fees, transaction costs, gross/net P&L, and R-multiples.
"""

from decimal import Decimal

import pytest

from alphaforge.core.exceptions import CostValidationError
from alphaforge.cost.calculator import (
    calculate_effective_price,
    calculate_fee,
    calculate_gross_pnl,
    calculate_net_pnl,
    calculate_r_multiples,
    calculate_transaction_costs,
)
from alphaforge.risk.enums import TradeSide

# --- calculate_effective_price tests ---


def test_calculate_effective_price_long_entry_and_exit() -> None:
    """LONG: entry pays higher (1 + s), exit receives lower (1 - s)."""
    ref_entry = Decimal("100.00")
    ref_exit = Decimal("110.00")
    slip_entry = Decimal("0.0010")  # 0.10%
    slip_exit = Decimal("0.0020")  # 0.20%

    eff_entry = calculate_effective_price(ref_entry, slip_entry, TradeSide.LONG, is_entry=True)
    # 100 * 1.0010 = 100.10
    assert eff_entry == Decimal("100.1000")

    eff_exit = calculate_effective_price(ref_exit, slip_exit, TradeSide.LONG, is_entry=False)
    # 110 * (1 - 0.0020) = 110 * 0.9980 = 109.78
    assert eff_exit == Decimal("109.7800")


def test_calculate_effective_price_short_entry_and_exit() -> None:
    """SHORT: entry sells lower (1 - s), exit buys higher (1 + s)."""
    ref_entry = Decimal("100.00")
    ref_exit = Decimal("90.00")
    slip_entry = Decimal("0.0010")  # 0.10%
    slip_exit = Decimal("0.0020")  # 0.20%

    eff_entry = calculate_effective_price(ref_entry, slip_entry, TradeSide.SHORT, is_entry=True)
    # 100 * (1 - 0.0010) = 99.90
    assert eff_entry == Decimal("99.9000")

    eff_exit = calculate_effective_price(ref_exit, slip_exit, TradeSide.SHORT, is_entry=False)
    # 90 * (1 + 0.0020) = 90 * 1.0020 = 90.18
    assert eff_exit == Decimal("90.1800")


def test_calculate_effective_price_zero_slippage() -> None:
    """Zero slippage preserves reference price exactly."""
    price = Decimal("24500.50")
    assert calculate_effective_price(price, Decimal("0"), TradeSide.LONG, is_entry=True) == price
    assert calculate_effective_price(price, Decimal("0"), TradeSide.LONG, is_entry=False) == price
    assert calculate_effective_price(price, Decimal("0"), TradeSide.SHORT, is_entry=True) == price
    assert calculate_effective_price(price, Decimal("0"), TradeSide.SHORT, is_entry=False) == price


def test_calculate_effective_price_invalid_inputs_fail_closed() -> None:
    """Non-positive or non-finite prices and negative slippage must raise CostValidationError."""
    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_effective_price(Decimal("0"), Decimal("0.01"), TradeSide.LONG, is_entry=True)

    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_effective_price(Decimal("-10.00"), Decimal("0.01"), TradeSide.LONG, is_entry=True)

    with pytest.raises(CostValidationError, match="cannot be negative"):
        calculate_effective_price(
            Decimal("100.00"), Decimal("-0.01"), TradeSide.LONG, is_entry=True
        )

    # 100% slippage on exit produces 0 price -> fails closed
    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_effective_price(Decimal("100.00"), Decimal("1.0"), TradeSide.LONG, is_entry=False)


# --- calculate_fee tests ---


def test_calculate_fee_valid() -> None:
    """Fee equals notional multiplied by fee rate."""
    notional = Decimal("500000.00")
    fee_rate = Decimal("0.0005")  # 0.05%
    fee = calculate_fee(notional, fee_rate)
    assert fee == Decimal("250.000000")


def test_calculate_fee_zero() -> None:
    """Zero notional or zero fee rate produces zero fee."""
    assert calculate_fee(Decimal("0"), Decimal("0.0005")) == Decimal("0")
    assert calculate_fee(Decimal("500000.00"), Decimal("0")) == Decimal("0")


def test_calculate_fee_invalid() -> None:
    """Negative notional or negative fee rate must raise CostValidationError."""
    with pytest.raises(CostValidationError, match="cannot be negative"):
        calculate_fee(Decimal("-100"), Decimal("0.01"))

    with pytest.raises(CostValidationError, match="cannot be negative"):
        calculate_fee(Decimal("100"), Decimal("-0.01"))


# --- calculate_transaction_costs tests ---


def test_calculate_transaction_costs_round_trip() -> None:
    """Transaction cost is sum of entry fee, exit fee, and fixed cost."""
    entry_notional = Decimal("250000.00")
    exit_notional = Decimal("260000.00")
    entry_fee_rate = Decimal("0.0002")  # 50.00
    exit_fee_rate = Decimal("0.0003")  # 78.00
    fixed_cost = Decimal("20.00")

    entry_fee, exit_fee, fixed, total = calculate_transaction_costs(
        entry_notional, exit_notional, entry_fee_rate, exit_fee_rate, fixed_cost
    )

    assert entry_fee == Decimal("50.000000")
    assert exit_fee == Decimal("78.000000")
    assert fixed == Decimal("20.00")
    assert total == Decimal("148.000000")


def test_calculate_transaction_costs_negative_fixed_cost_fails() -> None:
    """Negative fixed cost must raise CostValidationError."""
    with pytest.raises(CostValidationError, match="cannot be negative"):
        calculate_transaction_costs(
            Decimal("1000"), Decimal("1000"), Decimal("0"), Decimal("0"), Decimal("-5")
        )


# --- calculate_gross_pnl tests ---


def test_calculate_gross_pnl_long() -> None:
    """LONG gross P&L = (exit - entry) * qty * multiplier."""
    entry = Decimal("100.00")
    exit_p = Decimal("110.00")
    qty = 50
    mult = Decimal("2")

    # (110 - 100) * 50 * 2 = 10 * 100 = 1000
    pnl = calculate_gross_pnl(entry, exit_p, qty, mult, TradeSide.LONG)
    assert pnl == Decimal("1000.00")

    # Loss: entry 100, exit 95
    pnl_loss = calculate_gross_pnl(entry, Decimal("95.00"), qty, mult, TradeSide.LONG)
    assert pnl_loss == Decimal("-500.00")


def test_calculate_gross_pnl_short() -> None:
    """SHORT gross P&L = (entry - exit) * qty * multiplier."""
    entry = Decimal("100.00")
    exit_p = Decimal("90.00")
    qty = 25
    mult = Decimal("1")

    # (100 - 90) * 25 * 1 = 250
    pnl = calculate_gross_pnl(entry, exit_p, qty, mult, TradeSide.SHORT)
    assert pnl == Decimal("250.00")

    # Loss: entry 100, exit 105
    pnl_loss = calculate_gross_pnl(entry, Decimal("105.00"), qty, mult, TradeSide.SHORT)
    assert pnl_loss == Decimal("-125.00")


def test_calculate_gross_pnl_invalid_inputs_fail_closed() -> None:
    """Non-positive prices, quantity, or multiplier raise CostValidationError."""
    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_gross_pnl(Decimal("0"), Decimal("100"), 10, Decimal("1"), TradeSide.LONG)

    with pytest.raises(CostValidationError, match="strictly positive integer"):
        calculate_gross_pnl(Decimal("100"), Decimal("110"), 0, Decimal("1"), TradeSide.LONG)

    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_gross_pnl(Decimal("100"), Decimal("110"), 10, Decimal("0"), TradeSide.LONG)


# --- calculate_net_pnl tests ---


def test_calculate_net_pnl() -> None:
    """Net P&L = Gross P&L - Transaction Cost."""
    gross = Decimal("1000.00")
    friction = Decimal("75.50")
    assert calculate_net_pnl(gross, friction) == Decimal("924.50")

    # Loss aggravated by friction
    loss_gross = Decimal("-200.00")
    assert calculate_net_pnl(loss_gross, friction) == Decimal("-275.50")


def test_calculate_net_pnl_invalid_transaction_cost_fails() -> None:
    """Negative transaction cost raises CostValidationError."""
    with pytest.raises(CostValidationError, match="cannot be negative"):
        calculate_net_pnl(Decimal("100"), Decimal("-10"))


# --- calculate_r_multiples tests ---


def test_calculate_r_multiples() -> None:
    """R-multiples are gross/net P&L divided by risk amount."""
    gross_pnl = Decimal("2000.00")
    net_pnl = Decimal("1800.00")
    risk_amount = Decimal("1000.00")

    gross_r, net_r = calculate_r_multiples(gross_pnl, net_pnl, risk_amount)
    assert gross_r == Decimal("2.0")
    assert net_r == Decimal("1.8")


def test_calculate_r_multiples_invalid_risk_amount_fails() -> None:
    """Non-positive risk amount must raise CostValidationError."""
    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_r_multiples(Decimal("100"), Decimal("50"), Decimal("0"))

    with pytest.raises(CostValidationError, match="strictly positive"):
        calculate_r_multiples(Decimal("100"), Decimal("50"), Decimal("-100"))
