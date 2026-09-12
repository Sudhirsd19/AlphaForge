from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, strategies as st

from alphaforge.cost import CostConfig, CostInput, calculate_cost
from alphaforge.risk.enums import TradeSide

D = Decimal
TS = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def positive_decimal() -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=D("1"),
        max_value=D("100000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    )


def small_rate() -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=D("0"),
        max_value=D("0.05"),
        places=6,
        allow_nan=False,
        allow_infinity=False,
    )


def make_input(
    side: TradeSide,
    entry: Decimal,
    exit: Decimal,
    quantity: int,
    multiplier: Decimal,
    risk: Decimal,
    config: CostConfig,
) -> CostInput:
    return CostInput(
        side=side,
        entry_reference_price=entry,
        exit_reference_price=exit,
        quantity=quantity,
        contract_multiplier=multiplier,
        risk_amount=risk,
        cost_config=config,
        timestamp=TS,
    )


@given(
    price=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
)
def test_zero_cost_net_equals_gross(
    price: Decimal, quantity: int, multiplier: Decimal
) -> None:
    result = calculate_cost(
        make_input(
            TradeSide.LONG,
            price,
            price + D("1"),
            quantity,
            multiplier,
            D("100"),
            CostConfig(),
        )
    )
    assert result.net_pnl == result.gross_pnl


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
)
def test_costs_are_non_negative(
    entry: Decimal, exit: Decimal, quantity: int, multiplier: Decimal
) -> None:
    config = CostConfig(
        entry_fee_rate=D("0.01"),
        exit_fee_rate=D("0.01"),
        fixed_cost_per_trade=D("5"),
    )
    result = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity, multiplier, D("100"), config)
    )
    assert result.entry_fee >= 0
    assert result.exit_fee >= 0
    assert result.transaction_cost >= 0
    assert result.total_round_trip_cost >= 0


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 50),
    multiplier=positive_decimal(),
)
def test_quantity_scales_variable_costs(
    entry: Decimal, exit: Decimal, quantity: int, multiplier: Decimal
) -> None:
    config = CostConfig(entry_fee_rate=D("0.001"), exit_fee_rate=D("0.002"))
    base = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity, multiplier, D("100"), config)
    )
    double = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity * 2, multiplier, D("100"), config)
    )
    assert double.entry_notional == base.entry_notional * 2
    assert double.exit_notional == base.exit_notional * 2
    assert double.entry_fee == base.entry_fee * 2
    assert double.exit_fee == base.exit_fee * 2


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
    fee1=small_rate(),
    fee2=small_rate(),
)
def test_increasing_fee_rate_cannot_increase_net_pnl(
    entry: Decimal,
    exit: Decimal,
    quantity: int,
    multiplier: Decimal,
    fee1: Decimal,
    fee2: Decimal,
) -> None:
    low, high = sorted((fee1, fee2))
    base = CostConfig(entry_fee_rate=low, exit_fee_rate=low)
    more = CostConfig(entry_fee_rate=high, exit_fee_rate=high)
    a = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity, multiplier, D("100"), base)
    )
    b = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity, multiplier, D("100"), more)
    )
    assert b.net_pnl <= a.net_pnl


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
    s1=st.decimals(min_value=D("0"), max_value=D("0.4"), places=6),
    s2=st.decimals(min_value=D("0"), max_value=D("0.4"), places=6),
)
def test_increasing_slippage_cannot_improve_long_net_pnl(
    entry: Decimal,
    exit: Decimal,
    quantity: int,
    multiplier: Decimal,
    s1: Decimal,
    s2: Decimal,
) -> None:
    low, high = sorted((s1, s2))
    a = calculate_cost(
        make_input(
            TradeSide.LONG,
            entry,
            exit,
            quantity,
            multiplier,
            D("100"),
            CostConfig(entry_slippage_rate=low, exit_slippage_rate=low),
        )
    )
    b = calculate_cost(
        make_input(
            TradeSide.LONG,
            entry,
            exit,
            quantity,
            multiplier,
            D("100"),
            CostConfig(entry_slippage_rate=high, exit_slippage_rate=high),
        )
    )
    assert b.net_pnl <= a.net_pnl


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
)
def test_long_short_zero_cost_symmetry(
    entry: Decimal, exit: Decimal, quantity: int, multiplier: Decimal
) -> None:
    long = calculate_cost(
        make_input(TradeSide.LONG, entry, exit, quantity, multiplier, D("100"), CostConfig())
    )
    short = calculate_cost(
        make_input(TradeSide.SHORT, exit, entry, quantity, multiplier, D("100"), CostConfig())
    )
    assert long.gross_pnl == short.gross_pnl
    assert long.net_pnl == short.net_pnl


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
    risk=positive_decimal(),
)
def test_repeated_identical_inputs_are_equal(
    entry: Decimal, exit: Decimal, quantity: int, multiplier: Decimal, risk: Decimal
) -> None:
    config = CostConfig(
        entry_fee_rate=D("0.001"),
        exit_fee_rate=D("0.002"),
        entry_slippage_rate=D("0.003"),
        exit_slippage_rate=D("0.004"),
    )
    input_data = make_input(TradeSide.LONG, entry, exit, quantity, multiplier, risk, config)
    assert calculate_cost(input_data) == calculate_cost(input_data)


@given(
    entry=positive_decimal(),
    exit=positive_decimal(),
    quantity=st.integers(1, 100),
    multiplier=positive_decimal(),
)
def test_supplied_risk_is_the_only_r_denominator(
    entry: Decimal, exit: Decimal, quantity: int, multiplier: Decimal
) -> None:
    result = calculate_cost(
        make_input(
            TradeSide.LONG,
            entry,
            exit,
            quantity,
            multiplier,
            D("37"),
            CostConfig(),
        )
    )
    assert result.gross_R == result.gross_pnl / D("37")
    assert result.net_R == result.net_pnl / D("37")
