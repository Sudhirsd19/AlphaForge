"""
Comprehensive unit tests for AlphaForge Deterministic Risk Engine V1.
Tests all 28 core specifications, edge cases, limits, and fail-closed invariants.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.risk.engine import RiskEngine, evaluate_trade_risk
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import (
    CorrelatedGroupConfig,
    PortfolioRiskState,
    RiskConfig,
    RiskInput,
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
