"""
Comprehensive unit tests for AlphaForge Deterministic Risk Engine V1.
Tests all 28 core specifications, edge cases, limits, and fail-closed invariants.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.risk.engine import RiskEngine, evaluate_trade_risk
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import (
    CorrelatedGroupConfig,
    PortfolioRiskState,
    RiskConfig,
    RiskDecision,
    RiskInput,
    RiskReservation,
)


def make_valid_risk_input(
    signal_id: str = "SIG-001",
    symbol: str = "NIFTY",
    side: TradeSide = TradeSide.LONG,
    entry_price: Decimal = Decimal("24000.00"),
    stop_price: Decimal = Decimal("23880.00"),  # 120 pts (0.50%)
    contract_id: str = "NIFTY26JUNFUT",
    lot_size: int = 25,
    contract_multiplier: Decimal = Decimal("1"),
    account_equity: Decimal = Decimal("4000000.00"),
    available_capital: Decimal = Decimal("2000000.00"),
    proposed_quantity: int | None = 25,  # 1 lot: notional 600k (15% <= 20%), risk 3k
    evaluation_timestamp: datetime | None = None,
) -> RiskInput:
    """Helper to construct a valid baseline RiskInput."""
    ts = evaluation_timestamp or datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    return RiskInput(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        contract_id=contract_id,
        lot_size=lot_size,
        contract_multiplier=contract_multiplier,
        account_equity=account_equity,
        available_capital=available_capital,
        proposed_quantity=proposed_quantity,
        evaluation_timestamp=ts,
    )


def make_valid_portfolio_state(
    account_equity: Decimal = Decimal("4000000.00"),
    available_capital: Decimal = Decimal("2000000.00"),
    open_trade_count: int = 0,
    reserved_risk: Decimal = Decimal("0"),
    reserved_notional: Decimal = Decimal("0"),
    daily_starting_equity: Decimal = Decimal("4000000.00"),
    current_equity: Decimal = Decimal("4000000.00"),
    active_reservations: tuple[RiskReservation, ...] = (),
) -> PortfolioRiskState:
    """Helper to construct a valid baseline PortfolioRiskState."""
    return PortfolioRiskState(
        account_equity=account_equity,
        available_capital=available_capital,
        open_trade_count=open_trade_count,
        reserved_risk=reserved_risk,
        reserved_notional=reserved_notional,
        daily_starting_equity=daily_starting_equity,
        current_equity=current_equity,
        active_reservations=active_reservations,
    )


def test_1_valid_long_trade() -> None:
    """Test 1: Valid LONG trade within all risk limits is APPROVED."""
    trade_input = make_valid_risk_input(side=TradeSide.LONG)
    portfolio = make_valid_portfolio_state()
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.reason_code == RiskReasonCode.APPROVED
    assert decision.quantity == 25
    assert decision.risk_amount == Decimal("3000.00")
    # Notional = 24000 * 25 = 600,000 (15% <= 20% limit)


def test_1_valid_long_trade_with_proper_equity() -> None:
    """Test 1: Valid LONG trade with adequate equity for notional limit is APPROVED."""
    equity = Decimal("4000000.00")  # 4 Million
    # Risk budget = 4M * 0.5% = 20,000
    # Stop distance = 240 pts (1.00%). Qty = 25. Monetary risk = 240 * 25 = 6,000 <= 20,000
    # Notional = 24000 * 25 = 600,000 <= 4M * 20% = 800,000
    trade_input = make_valid_risk_input(
        side=TradeSide.LONG,
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),  # 240 pts
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.reason_code == RiskReasonCode.APPROVED
    assert decision.quantity == 25
    assert decision.risk_amount == Decimal("6000.00")
    assert decision.notional == Decimal("600000.00")


def test_2_valid_short_trade() -> None:
    """Test 2: Valid SHORT trade with stop above entry is APPROVED."""
    equity = Decimal("4000000.00")
    trade_input = make_valid_risk_input(
        side=TradeSide.SHORT,
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("24240.00"),  # 240 pts above entry
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.reason_code == RiskReasonCode.APPROVED
    assert decision.side == TradeSide.SHORT
    assert decision.risk_amount == Decimal("6000.00")


def test_3_invalid_equity() -> None:
    """Test 3: Non-positive or non-finite equity is rejected fail-closed."""
    trade_input = make_valid_risk_input(account_equity=Decimal("0"))
    portfolio = make_valid_portfolio_state()
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_EQUITY


def test_4_invalid_entry_price() -> None:
    """Test 4: Non-positive entry price is rejected fail-closed."""
    trade_input = make_valid_risk_input(entry_price=Decimal("-10.00"))
    portfolio = make_valid_portfolio_state()
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_ENTRY_PRICE


def test_5_invalid_stop_price() -> None:
    """Test 5: Non-positive stop price is rejected fail-closed."""
    trade_input = make_valid_risk_input(stop_price=Decimal("0"))
    portfolio = make_valid_portfolio_state()
    decision = evaluate_trade_risk(trade_input, portfolio)

    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_STOP_PRICE


def test_6_wrong_stop_direction() -> None:
    """Test 6: Stop loss on the wrong side of entry is rejected."""
    # LONG with stop > entry
    t_long = make_valid_risk_input(
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("24100.00"),
    )
    d_long = evaluate_trade_risk(t_long, make_valid_portfolio_state())
    assert d_long.decision == RiskDecisionState.REJECTED
    assert d_long.reason_code == RiskReasonCode.INVALID_STOP_DIRECTION

    # SHORT with stop < entry
    t_short = make_valid_risk_input(
        side=TradeSide.SHORT,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23900.00"),
    )
    d_short = evaluate_trade_risk(t_short, make_valid_portfolio_state())
    assert d_short.decision == RiskDecisionState.REJECTED
    assert d_short.reason_code == RiskReasonCode.INVALID_STOP_DIRECTION


def test_7_zero_risk_distance() -> None:
    """Test 7: Stop price equal to entry price is rejected."""
    t_zero = make_valid_risk_input(
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("24000.00"),
    )
    d_zero = evaluate_trade_risk(t_zero, make_valid_portfolio_state())
    assert d_zero.decision == RiskDecisionState.REJECTED
    assert d_zero.reason_code == RiskReasonCode.INVALID_STOP_DIRECTION


def test_8_minimum_stop_distance() -> None:
    """Test 8: Stop distance smaller than 0.10% of entry price is rejected."""
    # Entry = 24000, 0.10% = 24 pts. Let stop be 23990 (10 pts = 0.0417% < 0.10%)
    t_small = make_valid_risk_input(
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23990.00"),
    )
    d_small = evaluate_trade_risk(t_small, make_valid_portfolio_state())
    assert d_small.decision == RiskDecisionState.REJECTED
    assert d_small.reason_code == RiskReasonCode.STOP_DISTANCE_TOO_SMALL


def test_9_maximum_stop_distance() -> None:
    """Test 9: Stop distance larger than 3.00% of entry price is rejected."""
    # Entry = 24000, 3.00% = 720 pts. Let stop be 23200 (800 pts = 3.33% > 3.00%)
    t_large = make_valid_risk_input(
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23200.00"),
    )
    d_large = evaluate_trade_risk(t_large, make_valid_portfolio_state())
    assert d_large.decision == RiskDecisionState.REJECTED
    assert d_large.reason_code == RiskReasonCode.STOP_DISTANCE_TOO_LARGE


def test_10_trade_risk_cap() -> None:
    """Test 10: Proposed trade risk exceeding max_risk_per_trade is rejected."""
    equity = Decimal("1000000.00")  # Max risk = 1M * 0.5% = 5,000
    # Stop = 23800 (200 pts). Qty = 50 -> risk = 200 * 50 = 10,000 > 5,000
    t_cap = make_valid_risk_input(
        account_equity=equity,
        stop_price=Decimal("23800.00"),
        proposed_quantity=50,
    )
    d_cap = evaluate_trade_risk(t_cap, make_valid_portfolio_state(account_equity=equity))
    assert d_cap.decision == RiskDecisionState.REJECTED
    assert d_cap.reason_code == RiskReasonCode.RISK_LIMIT_EXCEEDED


def test_11_position_sizing_automated() -> None:
    """Test 11: Position sizing determines exact lot multiple when quantity is None."""
    equity = Decimal("4000000.00")  # Max risk = 20,000
    # Stop = 23840 (160 pts). Raw quantity = 20,000 / 160 = 125.
    # 125 % 25 == 0 -> sized exactly to 125 units!
    config = RiskConfig(max_single_position_notional=Decimal("0.80"))
    t_size = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23840.00"),
        proposed_quantity=None,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )
    decision = evaluate_trade_risk(t_size, portfolio, config=config)

    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.quantity == 125
    assert decision.risk_amount == Decimal("20000.00")


def test_12_lot_size_enforcement() -> None:
    """Test 12: Quantity not a multiple of lot size is rejected."""
    trade_input = make_valid_risk_input(
        lot_size=25,
        proposed_quantity=30,  # 30 % 25 != 0
    )
    decision = evaluate_trade_risk(trade_input, make_valid_portfolio_state())
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.INVALID_LOT_SIZE


def test_13_contract_multiplier() -> None:
    """Test 13: Contract multiplier scales risk and notional; non-positive rejected."""
    # Multiplier = 2 -> risk per unit = 120 * 2 = 240
    equity = Decimal("8000000.00")
    t_mult = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("4000000.00"),
        contract_multiplier=Decimal("2"),
        proposed_quantity=25,
    )
    decision = evaluate_trade_risk(
        t_mult,
        make_valid_portfolio_state(account_equity=equity, available_capital=Decimal("4000000.00")),
    )
    assert decision.decision == RiskDecisionState.APPROVED
    # Risk = 120 * 25 * 2 = 6,000
    assert decision.risk_amount == Decimal("6000.00")
    # Notional = 24000 * 25 * 2 = 1,200,000
    assert decision.notional == Decimal("1200000.00")

    # Invalid multiplier
    t_inv_mult = make_valid_risk_input(contract_multiplier=Decimal("0"))
    d_inv_mult = evaluate_trade_risk(t_inv_mult, make_valid_portfolio_state())
    assert d_inv_mult.decision == RiskDecisionState.INVALID
    assert d_inv_mult.reason_code == RiskReasonCode.INVALID_CONTRACT


def test_14_single_position_notional() -> None:
    """Test 14: Trade notional exceeding 20% of equity is rejected."""
    equity = Decimal("1000000.00")  # Max single notional = 200,000
    # Price = 24000, Qty = 25 -> Notional = 600,000 > 200,000
    t_notional = make_valid_risk_input(
        account_equity=equity,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23950.00"),  # Risk = 50 * 25 = 1250 <= 5000
        proposed_quantity=25,
    )
    decision = evaluate_trade_risk(t_notional, make_valid_portfolio_state(account_equity=equity))
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.POSITION_NOTIONAL_EXCEEDED


def test_15_portfolio_notional() -> None:
    """Test 15: Aggregate portfolio notional exceeding 100% of equity is rejected."""
    equity = Decimal("4000000.00")  # Max portfolio notional = 4,000,000
    # Existing reserved notional = 3,600,000 (90%)
    # Trade notional = 600,000 (15%) -> Total = 4,200,000 (105% > 100%)
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        reserved_notional=Decimal("3600000.00"),
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.PORTFOLIO_NOTIONAL_EXCEEDED


def test_16_portfolio_risk() -> None:
    """Test 16: Portfolio risk after trade exceeding 2.00% is rejected."""
    equity = Decimal("4000000.00")  # Max risk = 80,000 (2%)
    # Existing risk = 76,000 (1.9%)
    # Trade risk = 6,000 (0.15%) -> Total = 82,000 (2.05% > 2%)
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),  # 240 pts * 25 = 6,000
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        reserved_risk=Decimal("76000.00"),
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.PORTFOLIO_RISK_EXCEEDED


def test_17_maximum_open_trades() -> None:
    """Test 17: Exceeding 5 open trades/reservations is rejected."""
    equity = Decimal("4000000.00")
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )
    # Already 5 open trades
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        open_trade_count=5,
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.MAX_OPEN_TRADES_EXCEEDED


def test_18_available_capital() -> None:
    """Test 18: Required collateral exceeding available capital is rejected."""
    equity = Decimal("4000000.00")
    # Trade risk = 6000, 5% buffer -> required = 6300. But available = 5000.
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("5000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("5000.00"),
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.INSUFFICIENT_CAPITAL


def test_19_daily_loss_soft_limit() -> None:
    """Test 19: Daily loss >= 2.00% throttles trade risk budget by 50%."""
    # Starting = 4M, Current = 3.91M -> Loss = 90k (2.25% >= 2.00%)
    # Normal risk budget = 20,000. Throttled budget (50%) = 10,000.
    # Remaining before hard limit (3% = 120k) = 120k - 90k = 30k.
    # Effective max risk = min(10k, 30k) = 10,000.
    # If proposed trade risk is 12,000 (> 10,000) -> rejected with RISK_LIMIT_EXCEEDED!
    t_input = make_valid_risk_input(
        account_equity=Decimal("3910000.00"),
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23520.00"),  # 480 pts * 25 = 12,000
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=Decimal("3910000.00"),
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=Decimal("4000000.00"),
        current_equity=Decimal("3910000.00"),
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.RISK_LIMIT_EXCEEDED


def test_20_daily_loss_hard_limit() -> None:
    """Test 20: Daily loss >= 3.00% halts all trading with DAILY_LOSS_LIMIT_REACHED."""
    # Starting = 4M, Current = 3.87M -> Loss = 130k (3.25% >= 3.00%)
    t_input = make_valid_risk_input(
        account_equity=Decimal("3870000.00"),
        available_capital=Decimal("2000000.00"),
    )
    portfolio = make_valid_portfolio_state(
        account_equity=Decimal("3870000.00"),
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=Decimal("4000000.00"),
        current_equity=Decimal("3870000.00"),
    )
    decision = evaluate_trade_risk(t_input, portfolio)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.DAILY_LOSS_LIMIT_REACHED


def test_21_correlated_exposure() -> None:
    """Test 21: Trade exceeding configured correlation group risk is rejected."""
    equity = Decimal("8000000.00")
    cap = Decimal("4000000.00")
    group = CorrelatedGroupConfig(
        group_id="INDEX_GROUP",
        symbols=("NIFTY", "BANKNIFTY"),
        max_group_risk=Decimal("0.0050"),  # 0.50% = 40,000
    )
    config = RiskConfig(
        max_single_position_notional=Decimal("0.80"),
        correlated_groups=(group,),
    )

    # Suppose BANKNIFTY already has an active reservation of 36,000 (0.45% <= 0.50% trade limit)
    engine = RiskEngine(config=config)
    banknifty_input = make_valid_risk_input(
        signal_id="SIG-BN-01",
        symbol="BANKNIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23640.00"),  # 360 pts * 100 = 36,000
        lot_size=25,
        proposed_quantity=100,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d1, r1 = engine.request_risk_reservation(banknifty_input, portfolio)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    # Now attempt NIFTY trade with 6,000 risk -> Group risk after = 36k + 6k = 42k > 40k (0.50%)
    nifty_input = make_valid_risk_input(
        signal_id="SIG-NIFTY-01",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23760.00"),  # 240 pts * 25 = 6,000
        proposed_quantity=25,
    )
    d2, r2 = engine.request_risk_reservation(nifty_input, portfolio)
    assert d2.decision == RiskDecisionState.REJECTED
    assert d2.reason_code == RiskReasonCode.CORRELATED_RISK_EXCEEDED
    assert r2 is None


def test_22_duplicate_risk_reservation() -> None:
    """Test 22: Attempting to reserve the same signal_id twice is rejected idempotently."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    trade_input = make_valid_risk_input(
        signal_id="SIG-IDEMPOTENT-01",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    d1, r1 = engine.request_risk_reservation(trade_input, portfolio)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    # Second attempt with identical signal_id fails closed
    d2, r2 = engine.request_risk_reservation(trade_input, portfolio)
    assert d2.decision == RiskDecisionState.REJECTED
    assert d2.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    assert r2 is None


def test_23_missing_or_invalid_lot_size() -> None:
    """Test 23: Non-positive lot size is rejected with INVALID_LOT_SIZE."""
    trade_input = make_valid_risk_input(lot_size=-25)
    decision = evaluate_trade_risk(trade_input, make_valid_portfolio_state())
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_LOT_SIZE


def test_24_invalid_risk_state() -> None:
    """Test 24: Corrupted portfolio state (e.g. non-positive starting equity) rejected."""
    trade_input = make_valid_risk_input()
    portfolio = make_valid_portfolio_state(daily_starting_equity=Decimal("0"))
    decision = evaluate_trade_risk(trade_input, portfolio)
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.UNKNOWN_RISK_STATE


def test_25_decimal_precision() -> None:
    """Test 25: All financial fields strictly preserve fixed-point Decimal precision."""
    equity = Decimal("4000000.00")
    trade_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    decision = evaluate_trade_risk(
        trade_input,
        make_valid_portfolio_state(account_equity=equity, available_capital=Decimal("2000000.00")),
    )
    assert decision.decision == RiskDecisionState.APPROVED
    assert isinstance(decision.risk_amount, Decimal)
    assert isinstance(decision.notional, Decimal)
    assert isinstance(decision.portfolio_risk_after, Decimal)
    assert isinstance(decision.daily_loss, Decimal)


def test_26_deterministic_repeated_decisions() -> None:
    """Test 26: Repeated evaluations of the same inputs yield bit-for-bit identical decisions."""
    equity = Decimal("4000000.00")
    trade_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    decisions = [evaluate_trade_risk(trade_input, portfolio) for _ in range(50)]
    first = decisions[0]
    for d in decisions[1:]:
        assert d == first


def test_27_fail_closed_behavior() -> None:
    """Test 27: Unknown or missing risk state must NEVER produce APPROVED."""
    # When any gate fails, state is REJECTED or INVALID, never APPROVED
    equity = Decimal("4000000.00")
    t_bad = make_valid_risk_input(
        account_equity=equity,
        stop_price=Decimal("25000.00"),  # Wrong direction for LONG
    )
    d_bad = evaluate_trade_risk(t_bad, make_valid_portfolio_state(account_equity=equity))
    assert d_bad.decision != RiskDecisionState.APPROVED


def test_28_reservation_release() -> None:
    """Test 28: Releasing a reservation restores portfolio risk budget."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    trade_input = make_valid_risk_input(
        signal_id="SIG-RELEASE-01",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    d, r = engine.request_risk_reservation(trade_input, portfolio)
    assert d.decision == RiskDecisionState.APPROVED
    assert len(engine.get_active_reservations()) == 1

    # Release
    released = engine.release_reservation("SIG-RELEASE-01")
    assert released is True
    assert len(engine.get_active_reservations()) == 0

    # Can now reserve again
    d2, r2 = engine.request_risk_reservation(trade_input, portfolio)
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None


# ==============================================================================
# PHASE 5 FORENSIC REMEDIATION REGRESSION TESTS
# ==============================================================================


def test_remediation_blocker_1_missing_multiplier_rejected() -> None:
    """Blocker 1: Missing contract_multiplier must fail to construct and cannot be APPROVED."""
    # Attempting to construct RiskInput without contract_multiplier raises ValidationError
    with pytest.raises(ValidationError, match="contract_multiplier"):
        RiskInput(  # type: ignore[call-arg]
            signal_id="SIG-NO-MULT",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_price=Decimal("24000.00"),
            stop_price=Decimal("23880.00"),
            contract_id="NIFTY26JUNFUT",
            lot_size=25,
            account_equity=Decimal("4000000.00"),
            available_capital=Decimal("2000000.00"),
            evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        )


def test_remediation_blocker_1_invalid_multiplier_rejected() -> None:
    """Blocker 1: Non-positive or non-finite contract multiplier strictly produces INVALID."""
    portfolio = make_valid_portfolio_state()

    # Zero multiplier
    t_zero = make_valid_risk_input(contract_multiplier=Decimal("0"))
    d_zero = evaluate_trade_risk(t_zero, portfolio)
    assert d_zero.decision == RiskDecisionState.INVALID
    assert d_zero.reason_code == RiskReasonCode.INVALID_CONTRACT

    # Negative multiplier
    t_neg = make_valid_risk_input(contract_multiplier=Decimal("-2.5"))
    d_neg = evaluate_trade_risk(t_neg, portfolio)
    assert d_neg.decision == RiskDecisionState.INVALID
    assert d_neg.reason_code == RiskReasonCode.INVALID_CONTRACT


def test_remediation_blocker_2_inflated_equity_rejected() -> None:
    """Blocker 2: Inflated RiskInput equity mismatch must fail closed and NOT produce APPROVED."""
    t_inflated = make_valid_risk_input(account_equity=Decimal("5000000.00"))
    portfolio = make_valid_portfolio_state(account_equity=Decimal("4000000.00"))

    decision = evaluate_trade_risk(t_inflated, portfolio)
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_EQUITY


def test_remediation_blocker_2_reduced_equity_rejected() -> None:
    """Blocker 2: Reduced RiskInput equity mismatch must fail closed and NOT produce APPROVED."""
    t_reduced = make_valid_risk_input(account_equity=Decimal("3000000.00"))
    portfolio = make_valid_portfolio_state(account_equity=Decimal("4000000.00"))

    decision = evaluate_trade_risk(t_reduced, portfolio)
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INVALID_EQUITY


def test_remediation_blocker_2_inflated_capital_rejected() -> None:
    """Blocker 2: Inflated available capital mismatch must fail closed and NOT produce APPROVED."""
    t_inflated = make_valid_risk_input(available_capital=Decimal("3000000.00"))
    portfolio = make_valid_portfolio_state(available_capital=Decimal("2000000.00"))

    decision = evaluate_trade_risk(t_inflated, portfolio)
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INSUFFICIENT_CAPITAL


def test_remediation_blocker_2_reduced_capital_rejected() -> None:
    """Blocker 2: Reduced available capital mismatch must fail closed and NOT produce APPROVED."""
    t_reduced = make_valid_risk_input(available_capital=Decimal("1000000.00"))
    portfolio = make_valid_portfolio_state(available_capital=Decimal("2000000.00"))

    decision = evaluate_trade_risk(t_reduced, portfolio)
    assert decision.decision == RiskDecisionState.INVALID
    assert decision.reason_code == RiskReasonCode.INSUFFICIENT_CAPITAL


def test_remediation_blocker_3_correlated_risk_includes_unitemized_exposure() -> None:
    """
    Blocker 3: Correlated risk cannot ignore existing portfolio risk exposure
    even when active_reservations is empty.
    """
    equity = Decimal("8000000.00")
    cap = Decimal("4000000.00")
    group = CorrelatedGroupConfig(
        group_id="INDEX_GROUP",
        symbols=("NIFTY", "BANKNIFTY"),
        max_group_risk=Decimal("0.0050"),  # 0.50% = 40,000 max group risk
    )
    config = RiskConfig(
        max_single_position_notional=Decimal("0.80"),
        correlated_groups=(group,),
    )

    # Portfolio state has 36,000 of reserved_risk from prior session positions,
    # but active_reservations is empty ()
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("36000.00"),  # Existing group exposure unitemized
        active_reservations=(),
    )

    # Proposed NIFTY trade has 6,000 risk -> Group risk after = 36k + 6k = 42k > 40k
    nifty_input = make_valid_risk_input(
        signal_id="SIG-NIFTY-UNITEMIZED",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23760.00"),  # 240 pts * 25 = 6,000 risk
        proposed_quantity=25,
    )

    decision = evaluate_trade_risk(nifty_input, portfolio, config=config)
    # Must NOT ignore reserved_risk! Must be rejected with CORRELATED_RISK_EXCEEDED!
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.CORRELATED_RISK_EXCEEDED


def test_remediation_blocker_3_correlated_risk_with_non_group_reservations() -> None:
    """
    Blocker 3: Correlated risk correctly attributes unallocated reserved risk
    without misattributing explicit non-group reservations.
    """
    equity = Decimal("8000000.00")
    cap = Decimal("4000000.00")
    group = CorrelatedGroupConfig(
        group_id="INDEX_GROUP",
        symbols=("NIFTY", "BANKNIFTY"),
        max_group_risk=Decimal("0.0050"),  # 0.50% = 40,000 max group risk
    )
    config = RiskConfig(
        max_single_position_notional=Decimal("0.80"),
        correlated_groups=(group,),
    )

    # Portfolio has 50,000 total reserved risk.
    # active_reservations has one non-group reservation for TCS with 14,000 risk.
    # Unaccounted risk = 50,000 - 14,000 = 36,000.
    tcs_res = RiskReservation(
        reservation_id="RES-TCS-01",
        signal_id="SIG-TCS-01",
        symbol="TCS",
        side=TradeSide.LONG,
        entry_price=Decimal("3500.00"),
        stop_price=Decimal("3450.00"),
        quantity=280,
        monetary_risk=Decimal("14000.00"),
        notional=Decimal("980000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("50000.00"),
        active_reservations=(tcs_res,),
    )

    # Proposed NIFTY trade has 6,000 risk -> Group risk after = 36k + 6k = 42k > 40k
    nifty_input = make_valid_risk_input(
        signal_id="SIG-NIFTY-CORR-02",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23760.00"),  # 6,000 risk
        proposed_quantity=25,
    )

    decision = evaluate_trade_risk(nifty_input, portfolio, config=config)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.CORRELATED_RISK_EXCEEDED


def test_remediation_blocker_3_correlated_risk_no_double_counting() -> None:
    """
    Blocker 3: Correlated risk does not double count exposure when active_reservations
    fully accounts for reserved_risk.
    """
    equity = Decimal("8000000.00")
    cap = Decimal("4000000.00")
    group = CorrelatedGroupConfig(
        group_id="INDEX_GROUP",
        symbols=("NIFTY", "BANKNIFTY"),
        max_group_risk=Decimal("0.0050"),  # 0.50% = 40,000 max group risk
    )
    config = RiskConfig(
        max_single_position_notional=Decimal("0.80"),
        correlated_groups=(group,),
    )

    # 36,000 reserved_risk, and active_reservations explicitly holds the 36,000 reservation
    bn_res = RiskReservation(
        reservation_id="RES-BN-01",
        signal_id="SIG-BN-01",
        symbol="BANKNIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23640.00"),
        quantity=100,
        monetary_risk=Decimal("36000.00"),
        notional=Decimal("2400000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("36000.00"),
        active_reservations=(bn_res,),
    )

    # Proposed NIFTY trade has 2,000 risk -> Group risk after = 36k + 2k = 38k <= 40k
    # If double-counted, it would be 36k + 36k + 2k = 74k > 40k and reject!
    nifty_input = make_valid_risk_input(
        signal_id="SIG-NIFTY-NODBL",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23920.00"),  # 80 pts * 25 = 2,000 risk
        proposed_quantity=25,
    )

    decision = evaluate_trade_risk(nifty_input, portfolio, config=config)
    # Must NOT double count! Should be APPROVED
    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.risk_amount == Decimal("2000.00")


def test_remediation_improvement_4_lot_flooring_disabled_by_default() -> None:
    """Improvement 4: Under default policy, non-exact quantity rejects."""
    equity = Decimal("4000000.00")
    config = RiskConfig(allow_lot_flooring=False)  # Explicitly default

    # Stop = 23846.15 -> risk per unit = 153.85. Raw quantity = 20,000 / 153.85 = 130.00
    # 130 % 25 != 0 (not an exact lot multiple of 25)
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23846.15"),
        proposed_quantity=None,  # Request automated sizing
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    decision = evaluate_trade_risk(t_input, portfolio, config=config)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.INVALID_LOT_SIZE


def test_remediation_improvement_4_lot_flooring_enabled_floors_and_verifies_risk() -> None:
    """Improvement 4: When allow_lot_flooring=True, floors to whole lots and verifies risk."""
    equity = Decimal("4000000.00")
    config = RiskConfig(
        allow_lot_flooring=True,
        max_single_position_notional=Decimal("0.80"),
    )

    # Stop = 23846.15 -> risk per unit = 153.85. Raw quantity = 20,000 / 153.85 = 129.997...
    # Floor: 129 // 25 = 5 lots = 125 units.
    # Actual risk = 125 * 153.85 = 19,231.25 <= 20,000.00 budget!
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23846.15"),
        proposed_quantity=None,  # Request automated sizing
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    decision = evaluate_trade_risk(t_input, portfolio, config=config)
    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.quantity == 125  # Exactly 5 lots of 25
    assert decision.risk_amount is not None
    assert decision.risk_amount <= Decimal("20000.00")  # Strictly within budget


def test_remediation_improvement_4_lot_flooring_too_small_rejected() -> None:
    """Improvement 4: If affordable quantity is smaller than 1 lot, rejected even with flooring."""
    equity = Decimal("4000000.00")
    config = RiskConfig(allow_lot_flooring=True)

    # Very large stop distance (within 3% max): 600 pts
    # But lot size = 100 -> 1 lot risk = 600 * 100 = 60,000 > max risk 20,000
    # Affordable raw quantity = 20,000 / 600 = 33.33 units (< 100 lot_size)
    t_input = make_valid_risk_input(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23400.00"),  # 600 pts
        lot_size=100,
        proposed_quantity=None,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    decision = evaluate_trade_risk(t_input, portfolio, config=config)
    assert decision.decision == RiskDecisionState.REJECTED
    assert decision.reason_code == RiskReasonCode.POSITION_SIZE_TOO_SMALL


# ==============================================================================
# PHASE 5 FINAL FORENSIC REMEDIATION REGRESSION TESTS (H.1 - H.11)
# ==============================================================================


def test_remediation_final_1_duplicate_signal_id() -> None:
    """H.1: Submitting the same signal_id twice must be rejected with DUPLICATE_RISK_RESERVATION."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    trade_input = make_valid_risk_input(
        signal_id="SIG-FINAL-H1-01",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),
        proposed_quantity=25,
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    d1, r1 = engine.request_risk_reservation(trade_input, portfolio)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    d2, r2 = engine.request_risk_reservation(trade_input, portfolio)
    assert d2.decision == RiskDecisionState.REJECTED
    assert d2.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    assert r2 is None
    assert len(engine.get_active_reservations()) == 1
    assert engine.get_reservation("SIG-FINAL-H1-01") == r1


def test_remediation_final_2_duplicate_reservation_id() -> None:
    """H.2: Duplicate reservation_id cannot be created and must fail closed."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    trade_input_1 = make_valid_risk_input(
        signal_id="SIG-FINAL-H2-01",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    d1, r1 = engine.request_risk_reservation(trade_input_1, portfolio)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    # Verify query by reservation_id
    assert engine.get_reservation(r1.reservation_id) == r1

    # Attempting to submit another request mapping to the same reservation_id
    d2, r2 = engine.request_risk_reservation(trade_input_1, portfolio)
    assert d2.decision == RiskDecisionState.REJECTED
    assert d2.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    assert r2 is None


def test_remediation_final_3_portfolio_state_already_containing_same_reservation() -> None:
    """H.3: If portfolio_state already contains the reservation, new reservation is rejected."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()

    res = RiskReservation(
        reservation_id="RES-SIG-PORT-EXIST-01-NIFTY",
        signal_id="SIG-PORT-EXIST-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23880.00"),
        quantity=25,
        monetary_risk=Decimal("3000.00"),
        notional=Decimal("600000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        reserved_risk=Decimal("3000.00"),
        reserved_notional=Decimal("600000.00"),
        active_reservations=(res,),
    )

    # Attempting to reserve the same signal that already exists in portfolio_state
    trade_input = make_valid_risk_input(
        signal_id="SIG-PORT-EXIST-01",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    d, r = engine.request_risk_reservation(trade_input, portfolio)
    assert d.decision == RiskDecisionState.REJECTED
    assert d.reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    assert r is None
    assert len(engine.get_active_reservations()) == 0


def test_remediation_final_4_internal_reservation_plus_same_portfolio_reservation() -> None:
    """H.4: Internal reservation + same portfolio reservation must NOT double count."""
    equity = Decimal("4000000.00")
    cap = Decimal("2000000.00")
    # max_portfolio_risk = 0.0080 = 32,000 budget, max_risk_per_trade = 0.0050 = 20,000
    config = RiskConfig(
        max_risk_per_trade=Decimal("0.0050"),
        max_portfolio_risk=Decimal("0.0080"),
        max_single_position_notional=Decimal("0.80"),
    )
    engine = RiskEngine(config=config)

    # First trade: 15,000 risk
    trade_1 = make_valid_risk_input(
        signal_id="SIG-H4-01",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23400.00"),  # 600 pts * 25 = 15,000 risk
        proposed_quantity=25,
    )
    portfolio_1 = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d1, r1 = engine.request_risk_reservation(trade_1, portfolio_1)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    # Caller passes portfolio_state which ALSO includes r1
    portfolio_with_r1 = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("15000.00"),
        reserved_notional=r1.notional,
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=(r1,),
    )

    # Second trade: 10,000 risk.
    # Total combined: 15,000 + 10,000 = 25,000 <= 30,000 (APPROVED).
    # If double-counted: 15,000 (engine) + 15,000 (portfolio) + 10,000 = 40,000 > 30,000 (REJECTED).
    trade_2 = make_valid_risk_input(
        signal_id="SIG-H4-02",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23600.00"),  # 400 pts * 25 = 10,000 risk
        proposed_quantity=25,
    )

    d2, r2 = engine.request_risk_reservation(trade_2, portfolio_with_r1)
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None
    assert len(engine.get_active_reservations()) == 2


def test_remediation_final_5_same_request_concurrently_multiple_threads() -> None:
    """H.5: 20 threads submitting the same signal_id concurrently produce exactly 1 reservation."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    num_threads = 20
    results = []

    def worker() -> tuple[RiskDecision, RiskReservation | None]:
        t_input = make_valid_risk_input(
            signal_id="SIG-H5-CONCUR-SAME",
            account_equity=equity,
            available_capital=Decimal("2000000.00"),
        )
        return engine.request_risk_reservation(t_input, portfolio)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        for fut in as_completed(futures):
            results.append(fut.result())

    approved = [r for r in results if r[0].decision == RiskDecisionState.APPROVED]
    duplicates = [
        r
        for r in results
        if r[0].decision == RiskDecisionState.REJECTED
        and r[0].reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    ]

    assert len(approved) == 1
    assert len(duplicates) == num_threads - 1
    assert len(engine.get_active_reservations()) == 1


def test_remediation_final_6_two_different_valid_reservations() -> None:
    """H.6: Two different valid reservations are independently evaluated and tracked."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    portfolio = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    t1 = make_valid_risk_input(
        signal_id="SIG-H6-01",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23760.00"),  # 6,000 risk
        proposed_quantity=25,
    )
    t2 = make_valid_risk_input(
        signal_id="SIG-H6-02",
        symbol="BANKNIFTY",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        stop_price=Decimal("23800.00"),  # 5,000 risk
        proposed_quantity=25,
    )

    d1, r1 = engine.request_risk_reservation(t1, portfolio)
    d2, r2 = engine.request_risk_reservation(t2, portfolio)

    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None

    active = engine.get_active_reservations()
    assert len(active) == 2
    assert engine.total_reserved_risk == r1.monetary_risk + r2.monetary_risk
    assert engine.total_reserved_notional == r1.notional + r2.notional


def test_remediation_final_7_correlated_group_duplicate_reservation() -> None:
    """H.7: Correlated group does not double count when reservation is in both sources."""
    equity = Decimal("8000000.00")
    cap = Decimal("4000000.00")
    group = CorrelatedGroupConfig(
        group_id="INDEX_GROUP",
        symbols=("NIFTY", "BANKNIFTY"),
        max_group_risk=Decimal("0.0050"),  # 0.50% = 40,000 max group risk
    )
    config = RiskConfig(
        max_single_position_notional=Decimal("0.80"),
        correlated_groups=(group,),
    )
    engine = RiskEngine(config=config)

    # 1. BANKNIFTY reservation with 30,000 risk
    bn_input = make_valid_risk_input(
        signal_id="SIG-BN-H7",
        symbol="BANKNIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23700.00"),  # 300 pts * 100 = 30,000
        proposed_quantity=100,
    )
    port_base = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d_bn, r_bn = engine.request_risk_reservation(bn_input, port_base)
    assert d_bn.decision == RiskDecisionState.APPROVED
    assert r_bn is not None

    # 2. Portfolio passed includes r_bn in active_reservations and reserved_risk
    port_with_bn = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("30000.00"),
        reserved_notional=r_bn.notional,
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=(r_bn,),
    )

    # 3. New NIFTY trade with 8,000 risk.
    # Group risk after: 30,000 + 8,000 = 38,000 <= 40,000 (APPROVED).
    # If double counted: 30,000 + 30,000 + 8,000 = 68,000 > 40,000 (REJECTED).
    nifty_input = make_valid_risk_input(
        signal_id="SIG-NIFTY-H7",
        symbol="NIFTY",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23680.00"),  # 320 pts * 25 = 8,000 risk
        proposed_quantity=25,
    )

    d_nifty, r_nifty = engine.request_risk_reservation(nifty_input, port_with_bn)
    assert d_nifty.decision == RiskDecisionState.APPROVED
    assert r_nifty is not None


def test_remediation_final_8_open_trade_count_duplicate_reservation() -> None:
    """H.8: Max open trades count is not inflated by duplicate reservation representations."""
    equity = Decimal("4000000.00")
    cap = Decimal("2000000.00")
    config = RiskConfig(max_open_trades=2)
    engine = RiskEngine(config=config)

    # Trade 1 approved
    t1 = make_valid_risk_input(
        signal_id="SIG-H8-01",
        account_equity=equity,
        available_capital=cap,
    )
    port_base = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d1, r1 = engine.request_risk_reservation(t1, port_base)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    # Caller passes portfolio_state containing r1
    port_with_r1 = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=r1.monetary_risk,
        reserved_notional=r1.notional,
        open_trade_count=0,
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=(r1,),
    )

    # Trade 2 requested. With max_open_trades=2, active_count = 0 + 1 + 1 = 2 <= 2 (APPROVED).
    # If r1 is counted twice: active_count = 0 + 2 + 1 = 3 > 2 (REJECTED).
    t2 = make_valid_risk_input(
        signal_id="SIG-H8-02",
        account_equity=equity,
        available_capital=cap,
    )
    d2, r2 = engine.request_risk_reservation(t2, port_with_r1)
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None


def test_remediation_final_9_reserved_risk_duplicate_reservation() -> None:
    """H.9: Effective reserved_risk is not inflated when reservation exists in both sources."""
    equity = Decimal("4000000.00")
    cap = Decimal("2000000.00")
    # max_portfolio_risk = 0.0080 = 32,000 budget, max_risk_per_trade = 0.0060 = 24,000
    config = RiskConfig(
        max_risk_per_trade=Decimal("0.0060"),
        max_portfolio_risk=Decimal("0.0080"),
        max_single_position_notional=Decimal("0.80"),
    )
    engine = RiskEngine(config=config)

    t1 = make_valid_risk_input(
        signal_id="SIG-H9-01",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23600.00"),  # 400 pts (1.67%) * 50 = 20,000 risk
        proposed_quantity=50,
    )
    port_base = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d1, r1 = engine.request_risk_reservation(t1, port_base)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None

    port_with_r1 = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=Decimal("20000.00"),
        reserved_notional=r1.notional,
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=(r1,),
    )

    # Trade 2 with 8,000 risk: 20k + 8k = 28k <= 30k -> APPROVED
    # If double counted: 20k + 20k + 8k = 48k > 30k -> REJECTED
    t2 = make_valid_risk_input(
        signal_id="SIG-H9-02",
        account_equity=equity,
        available_capital=cap,
        stop_price=Decimal("23680.00"),  # 320 pts * 25 = 8,000 risk
        proposed_quantity=25,
    )
    d2, r2 = engine.request_risk_reservation(t2, port_with_r1)
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None


def test_remediation_final_10_reserved_notional_duplicate_reservation() -> None:
    """H.10: Effective reserved_notional is not inflated when reservation exists in both sources."""
    equity = Decimal("1000000.00")
    cap = Decimal("500000.00")
    config = RiskConfig(
        max_portfolio_notional=Decimal("0.60"),  # 600,000 budget
        max_single_position_notional=Decimal("0.50"),  # 500,000 limit
    )
    engine = RiskEngine(config=config)

    # Trade 1: Entry = 16,000, Qty = 25 -> Notional = 400,000
    t1 = make_valid_risk_input(
        signal_id="SIG-H10-01",
        account_equity=equity,
        available_capital=cap,
        entry_price=Decimal("16000.00"),
        stop_price=Decimal("15900.00"),  # 100 pts * 25 = 2,500 risk
        proposed_quantity=25,
    )
    port_base = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        daily_starting_equity=equity,
        current_equity=equity,
    )
    d1, r1 = engine.request_risk_reservation(t1, port_base)
    assert d1.decision == RiskDecisionState.APPROVED
    assert r1 is not None
    assert r1.notional == Decimal("400000.00")

    port_with_r1 = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=cap,
        reserved_risk=r1.monetary_risk,
        reserved_notional=Decimal("400000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
        active_reservations=(r1,),
    )

    # Trade 2: Entry = 6,000, Qty = 25 -> Notional = 150,000
    # Combined notional: 400,000 + 150,000 = 550,000 <= 600,000 (APPROVED)
    # If double counted: 400,000 + 400,000 + 150,000 = 950,000 > 600,000 (REJECTED)
    t2 = make_valid_risk_input(
        signal_id="SIG-H10-02",
        account_equity=equity,
        available_capital=cap,
        entry_price=Decimal("6000.00"),
        stop_price=Decimal("5900.00"),  # 100 pts * 25 = 2,500 risk
        proposed_quantity=25,
    )
    d2, r2 = engine.request_risk_reservation(t2, port_with_r1)
    assert d2.decision == RiskDecisionState.APPROVED
    assert r2 is not None


def test_remediation_final_11_inconsistent_reservation_state_fails_closed() -> None:
    """H.11: Inconsistent reservation state fails closed with RiskDecisionState.INVALID."""
    equity = Decimal("4000000.00")
    engine = RiskEngine()

    res_valid = RiskReservation(
        reservation_id="RES-INCONSISTENT-01",
        signal_id="SIG-INCONSISTENT-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23880.00"),
        quantity=25,
        monetary_risk=Decimal("3000.00"),
        notional=Decimal("600000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    # Subcase A: Conflicting monetary risk for same reservation_id
    res_conflict_risk = RiskReservation(
        reservation_id="RES-INCONSISTENT-01",
        signal_id="SIG-INCONSISTENT-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23880.00"),
        quantity=25,
        monetary_risk=Decimal("9999.00"),  # CONFLICT
        notional=Decimal("600000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )

    port_inconsistent = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        active_reservations=(res_valid, res_conflict_risk),
    )
    t_input = make_valid_risk_input(
        signal_id="SIG-NEW-TRADE",
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
    )

    d_conflict, r_conflict = engine.request_risk_reservation(t_input, port_inconsistent)
    assert d_conflict.decision == RiskDecisionState.INVALID
    assert d_conflict.reason_code == RiskReasonCode.UNKNOWN_RISK_STATE
    assert r_conflict is None

    # Subcase B: Conflicting symbol for same signal_id
    res_conflict_symbol = RiskReservation(
        reservation_id="RES-INCONSISTENT-DIFFID",
        signal_id="SIG-INCONSISTENT-01",  # Same signal ID as res_valid
        symbol="BANKNIFTY",  # CONFLICTING symbol
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23880.00"),
        quantity=25,
        monetary_risk=Decimal("3000.00"),
        notional=Decimal("600000.00"),
        created_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )
    port_inconsistent_sig = make_valid_portfolio_state(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        active_reservations=(res_valid, res_conflict_symbol),
    )
    d_sig_conflict, _ = engine.request_risk_reservation(t_input, port_inconsistent_sig)
    assert d_sig_conflict.decision == RiskDecisionState.INVALID
    assert d_sig_conflict.reason_code == RiskReasonCode.UNKNOWN_RISK_STATE

    # Subcase C: Evaluation directly via evaluate_trade_risk also fails closed
    d_direct = evaluate_trade_risk(t_input, port_inconsistent)
    assert d_direct.decision == RiskDecisionState.INVALID
    assert d_direct.reason_code == RiskReasonCode.UNKNOWN_RISK_STATE
