"""Deterministic Phase 6 cost-model unit tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.cost import (
    CALCULATION_VERSION,
    CostConfig,
    CostInput,
    CostValidationError,
    calculate_cost,
)
from alphaforge.cost.enums import CostReasonCode
from alphaforge.risk.enums import TradeSide

D = Decimal
TS = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def make_input(
    side: TradeSide = TradeSide.LONG,
    entry: Decimal = D("100"),
    exit: Decimal = D("110"),
    quantity: int = 10,
    multiplier: Decimal = D("2"),
    risk: Decimal = D("100"),
    config: CostConfig | None = None,
) -> CostInput:
    return CostInput(
        side=side,
        entry_reference_price=entry,
        exit_reference_price=exit,
        quantity=quantity,
        contract_multiplier=multiplier,
        risk_amount=risk,
        cost_config=config or CostConfig(),
        timestamp=TS,
    )


def test_long_zero_cost_and_gross_net_equality() -> None:
    result = calculate_cost(make_input())
    assert result.effective_entry_price == D("100")
    assert result.effective_exit_price == D("110")
    assert result.gross_pnl == D("200")
    assert result.net_pnl == D("200")
    assert result.gross_R == D("2")
    assert result.net_R == D("2")


def test_short_zero_cost() -> None:
    result = calculate_cost(make_input(TradeSide.SHORT, D("110"), D("100")))
    assert result.effective_entry_price == D("110")
    assert result.effective_exit_price == D("100")
    assert result.gross_pnl == D("200")
    assert result.net_pnl == D("200")


def test_long_entry_slippage_worsens_buy() -> None:
    result = calculate_cost(make_input(config=CostConfig(entry_slippage_rate=D("0.01"))))
    assert result.effective_entry_price == D("101")
    assert result.effective_exit_price == D("110")
    assert result.entry_slippage_cost == D("20")
    assert result.exit_slippage_cost == D("0")


def test_long_exit_slippage_worsens_sell() -> None:
    result = calculate_cost(make_input(config=CostConfig(exit_slippage_rate=D("0.01"))))
    assert result.effective_entry_price == D("100")
    assert result.effective_exit_price == D("108.90")
    assert result.exit_slippage_cost == D("22")


def test_short_entry_slippage_worsens_sell() -> None:
    result = calculate_cost(
        make_input(
            TradeSide.SHORT,
            D("110"),
            D("100"),
            config=CostConfig(entry_slippage_rate=D("0.01")),
        )
    )
    assert result.effective_entry_price == D("108.90")
    assert result.entry_slippage_cost == D("22")


def test_short_exit_slippage_worsens_buy() -> None:
    result = calculate_cost(
        make_input(
            TradeSide.SHORT,
            D("110"),
            D("100"),
            config=CostConfig(exit_slippage_rate=D("0.01")),
        )
    )
    assert result.effective_exit_price == D("101")
    assert result.exit_slippage_cost == D("20")


def test_entry_fee() -> None:
    result = calculate_cost(make_input(config=CostConfig(entry_fee_rate=D("0.001"))))
    assert result.entry_fee == D("2")
    assert result.exit_fee == D("0")


def test_exit_fee() -> None:
    result = calculate_cost(make_input(config=CostConfig(exit_fee_rate=D("0.002"))))
    assert result.entry_fee == D("0")
    assert result.exit_fee == D("4.40")


def test_fixed_cost_is_round_trip_and_charged_once() -> None:
    result = calculate_cost(make_input(config=CostConfig(fixed_cost_per_trade=D("5"))))
    assert result.fixed_cost == D("5")
    assert result.transaction_cost == D("5")
    assert result.net_pnl == result.gross_pnl - D("5")


def test_combined_fee_slippage_and_net_pnl() -> None:
    config = CostConfig(
        entry_fee_rate=D("0.001"),
        exit_fee_rate=D("0.002"),
        entry_slippage_rate=D("0.01"),
        exit_slippage_rate=D("0.02"),
        fixed_cost_per_trade=D("5"),
    )
    result = calculate_cost(make_input(config=config))
    assert result.entry_notional == D("2020")
    assert result.exit_notional == D("2156")
    assert result.entry_fee == D("2.02")
    assert result.exit_fee == D("4.312")
    assert result.transaction_cost == D("11.332")
    assert result.gross_pnl == D("136")
    assert result.net_pnl == D("124.668")
    assert result.total_slippage_cost == D("64")
    assert result.total_round_trip_cost == D("75.332")


def test_gross_r_and_net_r_use_supplied_risk() -> None:
    result = calculate_cost(make_input(risk=D("50")))
    assert result.gross_R == D("4")
    assert result.net_R == D("4")


def test_multiplier_scaling() -> None:
    baseline = calculate_cost(make_input(multiplier=D("2")))
    doubled = calculate_cost(make_input(multiplier=D("4")))
    assert doubled.entry_notional == baseline.entry_notional * 2
    assert doubled.exit_notional == baseline.exit_notional * 2
    assert doubled.gross_pnl == baseline.gross_pnl * 2


def test_quantity_scaling() -> None:
    config = CostConfig(entry_fee_rate=D("0.001"), exit_fee_rate=D("0.002"))
    baseline = calculate_cost(make_input(quantity=10, config=config))
    doubled = calculate_cost(make_input(quantity=20, config=config))
    assert doubled.entry_notional == baseline.entry_notional * 2
    assert doubled.exit_notional == baseline.exit_notional * 2
    assert doubled.transaction_cost == baseline.transaction_cost * 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_reference_price", D("0")),
        ("entry_reference_price", D("-1")),
        ("exit_reference_price", D("0")),
        ("exit_reference_price", D("-1")),
        ("quantity", 0),
        ("quantity", -1),
        ("contract_multiplier", D("0")),
        ("contract_multiplier", D("-1")),
        ("risk_amount", D("0")),
        ("risk_amount", D("-1")),
        ("entry_reference_price", D("NaN")),
        ("exit_reference_price", D("Infinity")),
    ],
)
def test_invalid_required_inputs_fail_closed(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "side": TradeSide.LONG,
        "entry_reference_price": D("100"),
        "exit_reference_price": D("110"),
        "quantity": 10,
        "contract_multiplier": D("2"),
        "risk_amount": D("100"),
        "cost_config": CostConfig(),
        "timestamp": TS,
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        CostInput(**kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "entry_fee_rate",
        "exit_fee_rate",
        "entry_slippage_rate",
        "exit_slippage_rate",
        "fixed_cost_per_trade",
    ],
)
def test_negative_cost_configuration_is_rejected(field: str) -> None:
    kwargs = {
        "entry_fee_rate": D("0"),
        "exit_fee_rate": D("0"),
        "entry_slippage_rate": D("0"),
        "exit_slippage_rate": D("0"),
        "fixed_cost_per_trade": D("0"),
    }
    kwargs[field] = D("-0.0001")
    with pytest.raises(ValidationError):
        CostConfig(**kwargs)


def test_effective_price_zero_fails_closed() -> None:
    config = CostConfig(exit_slippage_rate=D("1"))
    with pytest.raises(CostValidationError) as exc_info:
        calculate_cost(make_input(config=config))
    assert exc_info.value.reason_code is CostReasonCode.INVALID_EFFECTIVE_PRICE


def test_effective_price_zero_short_entry_fails_closed() -> None:
    config = CostConfig(entry_slippage_rate=D("1"))
    with pytest.raises(CostValidationError) as exc_info:
        calculate_cost(make_input(TradeSide.SHORT, D("110"), D("100"), config=config))
    assert exc_info.value.reason_code is CostReasonCode.INVALID_EFFECTIVE_PRICE


def test_decimal_determinism_and_immutable_input_result() -> None:
    config = CostConfig(
        entry_fee_rate=D("0.001"),
        exit_fee_rate=D("0.002"),
        entry_slippage_rate=D("0.003"),
        exit_slippage_rate=D("0.004"),
    )
    cost_input = make_input(config=config)
    first = calculate_cost(cost_input)
    second = calculate_cost(cost_input)
    assert first == second
    with pytest.raises(ValidationError):
        cost_input.quantity = 1
    with pytest.raises(ValidationError):
        first.net_pnl = D("0")


def test_utc_timestamp_and_metadata_propagate() -> None:
    data = make_input().model_dump()
    data.update(symbol="NIFTY", signal_id="SIG-001")
    result = calculate_cost(CostInput(**data))
    assert result.timestamp == TS
    assert result.symbol == "NIFTY"
    assert result.signal_id == "SIG-001"


def test_calculation_version_is_present() -> None:
    assert calculate_cost(make_input()).calculation_version == CALCULATION_VERSION


def test_missing_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        CostInput(
            side=TradeSide.LONG,
            entry_reference_price=D("100"),
            exit_reference_price=D("110"),
            quantity=10,
            contract_multiplier=D("2"),
            risk_amount=D("100"),
            cost_config=CostConfig(),
        )


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CostConfig(unknown=D("1"))


def test_long_short_directional_symmetry_at_zero_cost() -> None:
    long = calculate_cost(make_input(TradeSide.LONG, D("100"), D("110")))
    short = calculate_cost(make_input(TradeSide.SHORT, D("110"), D("100")))
    assert long.gross_pnl == short.gross_pnl
    assert long.net_pnl == short.net_pnl
    assert long.total_round_trip_cost == short.total_round_trip_cost


def test_slippage_is_adverse_for_long() -> None:
    baseline = calculate_cost(make_input())
    slipped = calculate_cost(
        make_input(
            config=CostConfig(entry_slippage_rate=D("0.01"), exit_slippage_rate=D("0.01"))
        )
    )
    assert slipped.net_pnl <= baseline.net_pnl
    assert slipped.total_slippage_cost > 0


def test_slippage_is_adverse_for_short() -> None:
    baseline = calculate_cost(make_input(TradeSide.SHORT, D("110"), D("100")))
    slipped = calculate_cost(
        make_input(
            TradeSide.SHORT,
            D("110"),
            D("100"),
            config=CostConfig(entry_slippage_rate=D("0.01"), exit_slippage_rate=D("0.01")),
        )
    )
    assert slipped.net_pnl <= baseline.net_pnl
    assert slipped.total_slippage_cost > 0


def test_transaction_cost_never_negative() -> None:
    result = calculate_cost(make_input())
    assert result.transaction_cost >= 0


def test_net_pnl_is_gross_minus_transaction_cost() -> None:
    config = CostConfig(entry_fee_rate=D("0.001"), exit_fee_rate=D("0.002"))
    result = calculate_cost(make_input(config=config))
    assert result.net_pnl == result.gross_pnl - result.transaction_cost


def test_total_round_trip_cost_is_slippage_plus_transaction_cost() -> None:
    config = CostConfig(
        entry_fee_rate=D("0.001"),
        exit_fee_rate=D("0.002"),
        entry_slippage_rate=D("0.003"),
        exit_slippage_rate=D("0.004"),
        fixed_cost_per_trade=D("5"),
    )
    result = calculate_cost(make_input(config=config))
    assert result.total_round_trip_cost == result.total_slippage_cost + result.transaction_cost
