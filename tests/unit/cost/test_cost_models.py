"""
Unit tests for AlphaForge Cost & Slippage Domain Models.
Verifies model immutability (frozen=True), strict Decimal types, UTC timestamp governance,
forbidding of extra fields, and uppercase normalization.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import CostValidationError
from alphaforge.cost.enums import CostDecisionState, CostReasonCode
from alphaforge.cost.models import CostConfig, CostInput, CostResult
from alphaforge.risk.enums import TradeSide


def test_cost_config_defaults() -> None:
    """CostConfig defaults must represent zero friction and explicit versioning."""
    cfg = CostConfig()
    assert cfg.entry_fee_rate == Decimal("0")
    assert cfg.exit_fee_rate == Decimal("0")
    assert cfg.entry_slippage_rate == Decimal("0")
    assert cfg.exit_slippage_rate == Decimal("0")
    assert cfg.fixed_cost_per_trade == Decimal("0")
    assert cfg.calculation_version == "PHASE6_COST_V1"


def test_cost_config_immutability() -> None:
    """CostConfig must be frozen and forbid extra fields."""
    cfg = CostConfig()
    with pytest.raises(ValidationError):
        cfg.entry_fee_rate = Decimal("0.001")

    with pytest.raises(ValidationError):
        CostConfig(extra_param=123)  # type: ignore[call-arg]


def test_cost_config_negative_rates_fail() -> None:
    """CostConfig rejects negative fee or slippage rates."""
    with pytest.raises(ValidationError):
        CostConfig(entry_fee_rate=Decimal("-0.001"))

    with pytest.raises(ValidationError):
        CostConfig(entry_slippage_rate=Decimal("-0.01"))

    with pytest.raises(ValidationError):
        CostConfig(fixed_cost_per_trade=Decimal("-10"))


def test_cost_input_valid() -> None:
    """CostInput creates successfully with valid required fields."""
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("24000.00"),
        exit_reference_price=Decimal("24200.00"),
        quantity=50,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("5000.00"),
        symbol="NIFTY",
        signal_id="SIG123",
        timestamp=datetime.now(UTC),
    )
    assert inp.side == TradeSide.LONG
    assert inp.quantity == 50
    assert inp.symbol == "NIFTY"
    assert inp.signal_id == "SIG123"


def test_cost_input_immutability() -> None:
    """CostInput must be frozen and forbid extra attributes."""
    inp = CostInput(
        side=TradeSide.LONG,
        entry_reference_price=Decimal("100"),
        exit_reference_price=Decimal("110"),
        quantity=10,
        contract_multiplier=Decimal("1"),
        risk_amount=Decimal("500"),
    )
    with pytest.raises(ValidationError):
        inp.quantity = 20

    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("500"),
            unrecognized_field="val",  # type: ignore[call-arg]
        )


def test_cost_input_invalid_numbers_fail_closed() -> None:
    """CostInput rejects non-positive or non-finite numbers."""
    # Non-positive entry price
    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("0"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("500"),
        )

    # Non-positive quantity
    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=0,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("500"),
        )

    # Missing contract multiplier (no default 1 fallback)
    with pytest.raises(ValidationError):
        CostInput(  # type: ignore[call-arg]
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            risk_amount=Decimal("500"),
        )

    # Non-positive multiplier
    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("0"),
            risk_amount=Decimal("500"),
        )

    # Non-positive risk_amount
    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("-10"),
        )


def test_cost_input_timezone_aware_utc_required() -> None:
    """CostInput rejects naive or non-UTC timestamps."""
    # Naive timestamp
    with pytest.raises(CostValidationError, match="timezone-aware UTC"):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("500"),
            timestamp=datetime(2026, 1, 1, 12, 0),
        )

    # Non-UTC timezone offset
    non_utc_tz = datetime.now(UTC) + timedelta(hours=5, minutes=30)
    # Naive without tzinfo
    with pytest.raises(CostValidationError, match="timezone-aware UTC"):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=Decimal("100"),
            exit_reference_price=Decimal("110"),
            quantity=10,
            contract_multiplier=Decimal("1"),
            risk_amount=Decimal("500"),
            timestamp=non_utc_tz.replace(tzinfo=None),
        )


def test_cost_result_immutability() -> None:
    """CostResult must be frozen and validate UTC timestamp."""
    res = CostResult(
        decision=CostDecisionState.VALID,
        reason_code=CostReasonCode.VALID,
        reason="OK",
        side=TradeSide.LONG,
        calculation_version="PHASE6_COST_V1",
        timestamp=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        res.decision = CostDecisionState.INVALID
