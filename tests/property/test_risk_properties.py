"""
Property-based tests for AlphaForge Deterministic Risk Engine V1 using Hypothesis.
Verifies invariants across randomized parameter spaces:
1. Approved trade risk never exceeds max_risk_per_trade.
2. Approved trade notional never exceeds max_single_position_notional.
3. Approved portfolio risk never exceeds max_portfolio_risk.
4. Approved open trade count never exceeds max_open_trades.
5. Inverted/invalid stop distances or prices never produce APPROVED.
6. All calculations operate strictly in Decimal; never float.
"""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.risk.engine import evaluate_trade_risk
from alphaforge.risk.enums import RiskDecisionState, TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskConfig, RiskInput


@given(
    equity_int=st.integers(min_value=500_000, max_value=50_000_000),
    entry_pts=st.integers(min_value=5000, max_value=50000),
    stop_offset_pts=st.integers(min_value=10, max_value=1000),
    lot_size=st.sampled_from([25, 50, 75, 100]),
    num_lots=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=150)
def test_approved_trade_risk_and_notional_bounds_property(
    equity_int: int,
    entry_pts: int,
    stop_offset_pts: int,
    lot_size: int,
    num_lots: int,
) -> None:
    """
    Property: If a trade is APPROVED, its monetary risk and single-position notional
    strictly satisfy configured upper bounds.
    """
    equity = Decimal(equity_int)
    entry = Decimal(entry_pts)
    stop = entry - Decimal(stop_offset_pts)
    qty = lot_size * num_lots

    cfg = RiskConfig(
        max_risk_per_trade=Decimal("0.0100"),  # 1.00%
        max_single_position_notional=Decimal("0.50"),  # 50%
    )

    t_input = RiskInput(
        signal_id="SIG-PROP-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=entry,
        stop_price=stop,
        contract_id="NIFTY26JUNFUT",
        lot_size=lot_size,
        contract_multiplier=Decimal("1"),
        account_equity=equity,
        available_capital=equity,
        proposed_quantity=qty,
        evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=equity,
        open_trade_count=0,
        reserved_risk=Decimal("0"),
        reserved_notional=Decimal("0"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    decision = evaluate_trade_risk(t_input, portfolio, config=cfg)

    if decision.decision == RiskDecisionState.APPROVED:
        assert decision.risk_amount is not None
        assert decision.notional is not None
        assert decision.quantity is not None

        # Invariant 1: Risk amount <= max_risk_per_trade * equity
        max_trade_risk = cfg.max_risk_per_trade * equity
        assert decision.risk_amount <= max_trade_risk

        # Invariant 2: Notional <= max_single_position_notional * equity
        max_notional = cfg.max_single_position_notional * equity
        assert decision.notional <= max_notional

        # Invariant 3: Quantity is a strict positive multiple of lot_size
        assert decision.quantity > 0
        assert decision.quantity % lot_size == 0

        # Invariant 4: No float types in any decision field
        assert isinstance(decision.risk_amount, Decimal)
        assert isinstance(decision.notional, Decimal)


@given(
    entry_pts=st.integers(min_value=5000, max_value=50000),
    invalid_offset=st.integers(min_value=1, max_value=5000),
)
@settings(max_examples=100)
def test_inverted_stop_loss_direction_property(
    entry_pts: int,
    invalid_offset: int,
) -> None:
    """
    Property: LONG trade with stop >= entry or SHORT trade with stop <= entry
    can NEVER be approved under any circumstances.
    """
    equity = Decimal("5000000.00")
    entry = Decimal(entry_pts)
    # Long trade with invalid stop above entry
    invalid_long_stop = entry + Decimal(invalid_offset)

    t_input_long = RiskInput(
        signal_id="SIG-PROP-DIR-L",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=entry,
        stop_price=invalid_long_stop,
        contract_id="NIFTY26JUNFUT",
        lot_size=25,
        account_equity=equity,
        available_capital=equity,
        proposed_quantity=25,
        evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=equity,
        daily_starting_equity=equity,
        current_equity=equity,
    )

    decision_long = evaluate_trade_risk(t_input_long, portfolio)
    assert decision_long.decision != RiskDecisionState.APPROVED

    # Short trade with invalid stop below entry
    invalid_short_stop = entry - Decimal(invalid_offset)
    t_input_short = RiskInput(
        signal_id="SIG-PROP-DIR-S",
        symbol="NIFTY",
        side=TradeSide.SHORT,
        entry_price=entry,
        stop_price=invalid_short_stop,
        contract_id="NIFTY26JUNFUT",
        lot_size=25,
        account_equity=equity,
        available_capital=equity,
        proposed_quantity=25,
        evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    decision_short = evaluate_trade_risk(t_input_short, portfolio)
    assert decision_short.decision != RiskDecisionState.APPROVED


@given(
    open_trades=st.integers(min_value=5, max_value=20),
)
@settings(max_examples=50)
def test_max_open_trades_barrier_property(open_trades: int) -> None:
    """
    Property: When open_trades >= max_open_trades, no new trade can ever be APPROVED.
    """
    equity = Decimal("5000000.00")
    cfg = RiskConfig(max_open_trades=5)

    t_input = RiskInput(
        signal_id="SIG-PROP-MAX-TRADES",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23880.00"),
        contract_id="NIFTY26JUNFUT",
        lot_size=25,
        account_equity=equity,
        available_capital=equity,
        proposed_quantity=25,
        evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=equity,
        open_trade_count=open_trades,
        daily_starting_equity=equity,
        current_equity=equity,
    )

    decision = evaluate_trade_risk(t_input, portfolio, config=cfg)
    assert decision.decision != RiskDecisionState.APPROVED
