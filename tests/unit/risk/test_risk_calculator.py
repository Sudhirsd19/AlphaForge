"""
Unit tests for AlphaForge Risk Calculator pure functions.
Verifies formulas for risk distance, stop percentage, position sizing,
notional exposure, daily loss, and capital requirements.
"""

from decimal import Decimal

import pytest

from alphaforge.core.exceptions import RiskValidationError
from alphaforge.risk.calculator import (
    calculate_daily_loss,
    calculate_max_trade_risk,
    calculate_notional,
    calculate_position_size,
    calculate_required_capital,
    calculate_risk_distance,
    calculate_risk_per_unit,
    calculate_stop_distance_pct,
)


def test_calculate_risk_distance_valid() -> None:
    """Verify absolute risk distance calculation in points."""
    dist = calculate_risk_distance(Decimal("24000.00"), Decimal("23900.00"))
    assert dist == Decimal("100.00")

    # Short side (entry < stop)
    dist_short = calculate_risk_distance(Decimal("24000.00"), Decimal("24100.00"))
    assert dist_short == Decimal("100.00")


def test_calculate_risk_distance_invalid_fails_closed() -> None:
    """Verify non-positive or equal prices fail closed."""
    # Equal prices -> zero risk distance
    with pytest.raises(RiskValidationError, match="strictly positive"):
        calculate_risk_distance(Decimal("24000.00"), Decimal("24000.00"))

    # Zero price
    with pytest.raises(RiskValidationError, match="strictly positive"):
        calculate_risk_distance(Decimal("0"), Decimal("24000.00"))

    # Negative price
    with pytest.raises(RiskValidationError, match="strictly positive"):
        calculate_risk_distance(Decimal("24000.00"), Decimal("-100.00"))


def test_calculate_stop_distance_pct() -> None:
    """Verify stop distance ratio relative to entry price."""
    # 240 / 24000 = 0.0100 (1.00%)
    ratio = calculate_stop_distance_pct(Decimal("24000.00"), Decimal("23760.00"))
    assert ratio == Decimal("0.01")


def test_calculate_max_trade_risk() -> None:
    """Verify maximum trade risk calculation."""
    # Equity = 1,000,000, risk 0.50% -> 5,000
    equity = Decimal("1000000.00")
    limit = Decimal("0.0050")
    max_risk = calculate_max_trade_risk(equity, limit)
    assert max_risk == Decimal("5000.0000")


def test_calculate_risk_per_unit() -> None:
    """Verify risk per unit accounting for contract multiplier."""
    # Distance = 100, multiplier = 2 -> 200 per unit
    rpu = calculate_risk_per_unit(Decimal("100.00"), Decimal("2.0"))
    assert rpu == Decimal("200.00")


def test_calculate_position_size_exact_lot_success() -> None:
    """Verify exact lot-aligned position sizing."""
    # Max risk = 5000, risk_per_unit = 100 -> raw_quantity = 50. Lot size = 25.
    # 50 % 25 == 0 -> quantity = 50
    qty = calculate_position_size(
        max_trade_risk=Decimal("5000.00"),
        risk_per_unit=Decimal("100.00"),
        lot_size=25,
    )
    assert qty == 50


def test_calculate_position_size_sub_lot_rejected() -> None:
    """Verify raw quantity below 1 lot is rejected fail-closed."""
    # Max risk = 1000, risk_per_unit = 100 -> raw_quantity = 10 < lot_size 25
    with pytest.raises(RiskValidationError, match="smaller than lot_size"):
        calculate_position_size(
            max_trade_risk=Decimal("1000.00"),
            risk_per_unit=Decimal("100.00"),
            lot_size=25,
        )


def test_calculate_position_size_non_multiple_rejected_without_silent_flooring() -> None:
    """Verify fractional lots are rejected when silent flooring is forbidden."""
    # Max risk = 3500, risk_per_unit = 100 -> raw_quantity = 35. Lot size = 25.
    # 35 % 25 != 0 -> rejected without silent flooring
    with pytest.raises(RiskValidationError, match="not an exact multiple"):
        calculate_position_size(
            max_trade_risk=Decimal("3500.00"),
            risk_per_unit=Decimal("100.00"),
            lot_size=25,
            allow_lot_floor=False,
        )

    # When allow_lot_floor is explicitly True:
    qty_floored = calculate_position_size(
        max_trade_risk=Decimal("3500.00"),
        risk_per_unit=Decimal("100.00"),
        lot_size=25,
        allow_lot_floor=True,
    )
    assert qty_floored == 25


def test_calculate_notional() -> None:
    """Verify notional calculation."""
    # Price = 24000, Qty = 50, Multiplier = 1 -> 1,200,000
    notional = calculate_notional(Decimal("24000.00"), 50, Decimal("1"))
    assert notional == Decimal("1200000.00")


def test_calculate_daily_loss() -> None:
    """Verify daily loss and percentage calculation."""
    # Starting = 1,000,000, Current = 980,000 -> Loss = 20,000 (2%)
    loss, loss_pct = calculate_daily_loss(Decimal("1000000.00"), Decimal("980000.00"))
    assert loss == Decimal("20000.00")
    assert loss_pct == Decimal("0.02")

    # In profit (current > starting) -> Loss = 0
    profit_loss, profit_loss_pct = calculate_daily_loss(
        Decimal("1000000.00"), Decimal("1050000.00")
    )
    assert profit_loss == Decimal("0")
    assert profit_loss_pct == Decimal("0")


def test_calculate_required_capital() -> None:
    """Verify capital requirement with 5% risk reserve buffer."""
    # Risk = 5,000, Buffer = 5% -> 5000 * 1.05 = 5250
    req = calculate_required_capital(Decimal("5000.00"), Decimal("0.05"))
    assert req == Decimal("5250.00")
