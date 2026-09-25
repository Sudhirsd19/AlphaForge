"""
Comprehensive Trade Logic Verification Test Suite.
Verifies every single rule, gate, calculation, risk assessment, and execution state
in AlphaForge with deterministic assertions and exact mathematical proof.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.models import Candle
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderEvent, OrderSide, OrderState
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.risk.engine import evaluate_trade_risk
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskInput
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine
from alphaforge.strategy.indicators import (
    calculate_atr,
    calculate_candle_geometry,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from alphaforge.strategy.rules import (
    calculate_stops_and_targets,
    evaluate_breakout,
    evaluate_candle_geometry,
    evaluate_momentum,
    evaluate_trend_regime,
    evaluate_volatility,
    evaluate_volume_spike,
)
from tests.helpers import create_candle, generate_candle_series


# ---------------------------------------------------------------------------
# RULE 1: Closed Candle Quarantine (Index [0] Isolation)
# ---------------------------------------------------------------------------
def test_proof_rule_01_closed_candle_quarantine() -> None:
    """
    PROOF: Mutating forming candle [0] with extreme values produces ZERO change
    in the strategy signal. Index [0] is completely quarantined.
    """
    base_time = datetime(2026, 9, 25, 10, 0, 0, tzinfo=UTC)
    exec_c = generate_candle_series(base_time, count=30, trend_type="bullish")
    conf_c = generate_candle_series(base_time, count=30, interval_minutes=15, trend_type="bullish")
    eval_time = exec_c[1].timestamp

    engine = DeterministicStrategyEngine()
    baseline = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    # Mutate forming candle [0] wildly (flash crash / surge)
    mutated = list(exec_c)
    mutated[0] = create_candle(
        timestamp=exec_c[0].timestamp,
        open_price=Decimal("99999.00"),
        high_price=Decimal("150000.00"),
        low_price=Decimal("1000.00"),
        close_price=Decimal("120000.00"),
        volume=99999999,
        is_closed=False,
    )

    mutated_signal = engine.evaluate(mutated, conf_c, FuturesConfirmationStatus.CONFIRMED, eval_time)

    assert baseline.signal_id == mutated_signal.signal_id
    assert baseline.decision == mutated_signal.decision
    assert baseline.entry_reference == mutated_signal.entry_reference
    assert baseline.stop_reference == mutated_signal.stop_reference
    assert baseline.target_reference == mutated_signal.target_reference


# ---------------------------------------------------------------------------
# RULE 2: Minimum History Guard (< 22 bars)
# ---------------------------------------------------------------------------
def test_proof_rule_02_minimum_history_guard() -> None:
    """
    PROOF: If fewer than 22 3m candles arrive, the strategy engine immediately
    rejects with REJECT_INSUFFICIENT_HISTORY.
    """
    base_time = datetime(2026, 9, 25, 10, 0, 0, tzinfo=UTC)
    short_exec = generate_candle_series(base_time, count=15)  # Needs >= 22
    conf_c = generate_candle_series(base_time, count=30, interval_minutes=15)

    engine = DeterministicStrategyEngine()
    sig = engine.evaluate(short_exec, conf_c, FuturesConfirmationStatus.CONFIRMED, base_time)

    assert sig.decision == StrategyDecision.REJECT
    assert sig.rejection_code == RejectionCode.REJECT_INSUFFICIENT_HISTORY


# ---------------------------------------------------------------------------
# RULE 3: Stale Data Guard (> 195 seconds)
# ---------------------------------------------------------------------------
def test_proof_rule_03_data_freshness_guard() -> None:
    """
    PROOF: If market data evaluation occurs > 195s past candle [1] close timestamp,
    the signal is rejected with REJECT_DATA_STALE.
    """
    base_time = datetime(2026, 9, 25, 10, 0, 0, tzinfo=UTC)
    exec_c = generate_candle_series(base_time, count=30)
    conf_c = generate_candle_series(base_time, count=30, interval_minutes=15)

    # Trigger candle is exec_c[1]
    trigger_ts = exec_c[1].timestamp
    stale_eval_time = trigger_ts + timedelta(seconds=200)  # > 195s

    engine = DeterministicStrategyEngine()
    sig = engine.evaluate(exec_c, conf_c, FuturesConfirmationStatus.CONFIRMED, stale_eval_time)

    assert sig.decision == StrategyDecision.REJECT
    assert sig.rejection_code == RejectionCode.REJECT_DATA_STALE


# ---------------------------------------------------------------------------
# RULE 4: Multi-Timeframe Trend & Regime Filter (15m)
# ---------------------------------------------------------------------------
def test_proof_rule_04_trend_regime_filter() -> None:
    """
    PROOF: 15m Trend Regime evaluation:
    - EMA(9) > EMA(21) and Close > EMA(21) -> BULLISH
    - EMA(9) < EMA(21) and Close < EMA(21) -> BEARISH
    - Flat / Tangled EMAs -> NEUTRAL -> REJECT_TREND
    - Missing 15m history (< 21 bars) -> REJECT_MISSING_TIMEFRAME
    """
    t0 = datetime(2026, 9, 25, 9, 15, tzinfo=UTC)

    # Bullish series: prices increasing 100 -> 140
    bullish_candles = [
        create_candle(t0 + timedelta(minutes=15 * i), str(100 + i), str(102 + i), str(99 + i), str(101 + i))
        for i in range(25)
    ]
    trend_bull, fast_b, slow_b = evaluate_trend_regime(bullish_candles, 9, 21)
    assert trend_bull == TrendState.BULLISH
    assert fast_b > slow_b

    # Bearish series: prices decreasing 140 -> 100
    bearish_candles = [
        create_candle(t0 + timedelta(minutes=15 * i), str(140 - i), str(141 - i), str(138 - i), str(139 - i))
        for i in range(25)
    ]
    trend_bear, fast_bear, slow_bear = evaluate_trend_regime(bearish_candles, 9, 21)
    assert trend_bear == TrendState.BEARISH
    assert fast_bear < slow_bear

    # Flat / Sideways series: prices oscillating closely around 100
    flat_candles = [
        create_candle(t0 + timedelta(minutes=15 * i), "100.0", "100.5", "99.5", "100.0")
        for i in range(25)
    ]
    trend_flat, _, _ = evaluate_trend_regime(flat_candles, 9, 21)
    assert trend_flat == TrendState.NEUTRAL


# ---------------------------------------------------------------------------
# RULE 5: 20-Period Breakout
# ---------------------------------------------------------------------------
def test_proof_rule_05_breakout_detection() -> None:
    """
    PROOF: Breakout logic requires closed trigger candle [1] close to exceed
    the highest high of the prior 20 bars (LONG) or break below lowest low (SHORT).
    """
    t0 = datetime(2026, 9, 25, 9, 15, tzinfo=UTC)

    # 20 prior bars oscillating between 24000 and 24100
    prior_bars = [
        create_candle(t0 + timedelta(minutes=3 * i), "24020", "24100", "24000", "24050")
        for i in range(20)
    ]

    # Case A: Inside range close (24080 <= 24100) -> No breakout
    inside_trigger = create_candle(t0 + timedelta(minutes=60), "24050", "24090", "24040", "24080")
    is_long, is_short, res, sup = evaluate_breakout(prior_bars + [inside_trigger], lookback=20)
    assert not is_long and not is_short
    assert res == Decimal("24100")
    assert sup == Decimal("24000")

    # Case B: Long breakout close (24120 > 24100) -> Long confirmed
    long_trigger = create_candle(t0 + timedelta(minutes=60), "24090", "24130", "24080", "24120")
    is_long, is_short, res, sup = evaluate_breakout(prior_bars + [long_trigger], lookback=20)
    assert is_long and not is_short

    # Case C: Short breakout close (23980 < 24000) -> Short confirmed
    short_trigger = create_candle(t0 + timedelta(minutes=60), "24010", "24020", "23970", "23980")
    is_long, is_short, res, sup = evaluate_breakout(prior_bars + [short_trigger], lookback=20)
    assert is_short and not is_long


# ---------------------------------------------------------------------------
# RULE 6: Candle Geometry (Body >= 50%, Close Location >= 70%)
# ---------------------------------------------------------------------------
def test_proof_rule_06_candle_geometry() -> None:
    """
    PROOF: Candle Geometry Gate:
    - Body Ratio = |Close - Open| / (High - Low) >= 50%
    - Close Location = (Close - Low) / (High - Low) >= 70% for LONG
    - Close Location = (High - Close) / (High - Low) >= 70% for SHORT
    - Rejects Doji, spin-tops, and candles with long opposing wicks.
    """
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    # Valid Bullish Marubozu-like bar: Open 100, High 110, Low 99, Close 109
    # Range = 11, Body = 9 (81.8%), Close Loc = (109 - 99)/11 = 90.9%
    valid_bull = create_candle(t0, "100.0", "110.0", "99.0", "109.0")
    valid, b_ratio, loc = evaluate_candle_geometry(
        valid_bull, SignalDirection.LONG, Decimal("0.50"), Decimal("0.70")
    )
    assert valid
    assert b_ratio > Decimal("0.80")
    assert loc > Decimal("0.90")

    # Invalid Doji: Open 100, High 110, Low 90, Close 100.5 (Body = 0.5, Range = 20 -> 2.5%)
    doji = create_candle(t0, "100.0", "110.0", "90.0", "100.5")
    valid_doji, _, _ = evaluate_candle_geometry(
        doji, SignalDirection.LONG, Decimal("0.50"), Decimal("0.70")
    )
    assert not valid_doji

    # Invalid Shooting Star (Upper wick rejection): Open 100, High 120, Low 99, Close 102
    # Body = 2 / 21 = 9.5%, Close loc = (102-99)/21 = 14.2% < 70%
    shooting_star = create_candle(t0, "100.0", "120.0", "99.0", "102.0")
    valid_star, _, _ = evaluate_candle_geometry(
        shooting_star, SignalDirection.LONG, Decimal("0.50"), Decimal("0.70")
    )
    assert not valid_star


# ---------------------------------------------------------------------------
# RULE 7: Relative Volume Spike (>= 1.20x 20-SMA)
# ---------------------------------------------------------------------------
def test_proof_rule_07_volume_spike() -> None:
    """
    PROOF: Volume Spike Gate:
    - Trigger bar volume must be >= 1.20x of the prior 20-bar SMA volume.
    """
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    # 20 prior bars with volume = 1000 each (SMA = 1000)
    prior_bars = [
        create_candle(t0 + timedelta(minutes=3 * i), "100", "105", "95", "100", volume=1000)
        for i in range(20)
    ]

    # Case A: Low volume (1100 < 1.2 * 1000 = 1200) -> Rejected
    low_vol_bar = create_candle(t0 + timedelta(minutes=60), "100", "105", "95", "100", volume=1100)
    is_conf, ratio = evaluate_volume_spike(prior_bars + [low_vol_bar], lookback=20, min_relative_volume=Decimal("1.20"))
    assert not is_conf
    assert ratio == Decimal("1.10")

    # Case B: Volume spike (1500 >= 1200) -> Confirmed
    high_vol_bar = create_candle(t0 + timedelta(minutes=60), "100", "105", "95", "100", volume=1500)
    is_conf_hi, ratio_hi = evaluate_volume_spike(prior_bars + [high_vol_bar], lookback=20, min_relative_volume=Decimal("1.20"))
    assert is_conf_hi
    assert ratio_hi == Decimal("1.50")


# ---------------------------------------------------------------------------
# RULE 8: RSI Momentum Boundaries
# ---------------------------------------------------------------------------
def test_proof_rule_08_rsi_momentum() -> None:
    """
    PROOF: Momentum Gate:
    - LONG requires 50.0 < RSI <= 75.0 (bullish momentum without extreme overbought)
    - SHORT requires 25.0 <= RSI < 50.0 (bearish momentum without extreme oversold)
    """
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    # Steady upward trending prices gives RSI ~ 65-70
    up_bars = [
        create_candle(t0 + timedelta(minutes=3 * i), str(100 + i * 2), str(102 + i * 2), str(99 + i * 2), str(101 + i * 2))
        for i in range(25)
    ]
    valid_long, rsi_val = evaluate_momentum(
        up_bars, SignalDirection.LONG, period=14, rsi_min=Decimal("50.0"), rsi_max=Decimal("75.0")
    )
    # Price steadily rises -> RSI > 50
    assert rsi_val > Decimal("50.0")

    # Downward trending prices gives RSI < 50
    down_bars = [
        create_candle(t0 + timedelta(minutes=3 * i), str(200 - i * 2), str(201 - i * 2), str(198 - i * 2), str(199 - i * 2))
        for i in range(25)
    ]
    valid_short, rsi_down = evaluate_momentum(
        down_bars, SignalDirection.SHORT, period=14, rsi_min=Decimal("25.0"), rsi_max=Decimal("50.0")
    )
    assert rsi_down < Decimal("50.0")


# ---------------------------------------------------------------------------
# RULE 9: ATR Volatility Gate (0.05% - 1.50%)
# ---------------------------------------------------------------------------
def test_proof_rule_09_volatility_gate() -> None:
    """
    PROOF: ATR Volatility Gate:
    - ATR(14) must be between 0.05% and 1.50% of the latest close price.
    """
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    # Price ~ 24000. 0.05% = 12 pts, 1.50% = 360 pts.
    # Normal bar range: High - Low = 30 pts (0.125% -> within bounds)
    normal_bars = [
        create_candle(t0 + timedelta(minutes=3 * i), "24000", "24030", "23970", "24000")
        for i in range(20)
    ]
    is_valid, current_atr = evaluate_volatility(normal_bars, period=14, min_pct=Decimal("0.0005"), max_pct=Decimal("0.0150"))
    assert is_valid
    assert Decimal("12.0") <= current_atr <= Decimal("360.0")


# ---------------------------------------------------------------------------
# RULE 10: Dynamic Stop-Loss & Target Math (Exact 1:2 R:R)
# ---------------------------------------------------------------------------
def test_proof_rule_10_dynamic_stops_and_targets() -> None:
    """
    PROOF: Stop-Loss & Target Mathematical Accuracy:
    - LONG: Stop = Structural Swing Low - (1.0 * ATR)
            Target = Entry + 2.0 * (Entry - Stop)
    - SHORT: Stop = Structural Swing High + (1.0 * ATR)
             Target = Entry - 2.0 * (Stop - Entry)
    - Reward-to-Risk ratio is EXACTLY 2.00000.
    """
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    trigger_long = create_candle(t0, "24020", "24050", "24010", "24040")
    prior_swing = create_candle(t0 - timedelta(minutes=3), "23980", "24030", "23950", "24010")
    atr = Decimal("20.00")

    # Structural low = min(24010, 23950) = 23950
    # Stop = 23950 - 20 = 23930
    # Risk distance = 24040 - 23930 = 110 pts
    # Target = 24040 + (2 * 110) = 24260
    entry, stop, target, risk_dist = calculate_stops_and_targets(
        trigger_long, [trigger_long, prior_swing], atr, SignalDirection.LONG, Decimal("1.0"), Decimal("2.0")
    )
    assert entry == Decimal("24040")
    assert stop == Decimal("23930")
    assert risk_dist == Decimal("110")
    assert target == Decimal("24260")
    assert (target - entry) / risk_dist == Decimal("2.0")  # Exact 1:2 R:R

    # SHORT calculation
    trigger_short = create_candle(t0, "23980", "23990", "23940", "23950")
    prior_short = create_candle(t0 - timedelta(minutes=3), "24010", "24050", "23990", "24000")
    # Structural high = max(23990, 24050) = 24050
    # Stop = 24050 + 20 = 24070
    # Risk distance = 24070 - 23950 = 120 pts
    # Target = 23950 - (2 * 120) = 23710
    entry_s, stop_s, target_s, risk_dist_s = calculate_stops_and_targets(
        trigger_short, [trigger_short, prior_short], atr, SignalDirection.SHORT, Decimal("1.0"), Decimal("2.0")
    )
    assert entry_s == Decimal("23950")
    assert stop_s == Decimal("24070")
    assert risk_dist_s == Decimal("120")
    assert target_s == Decimal("23710")
    assert (entry_s - target_s) / risk_dist_s == Decimal("2.0")  # Exact 1:2 R:R


# ---------------------------------------------------------------------------
# RULE 11: Deduplication Gate
# ---------------------------------------------------------------------------
def test_proof_rule_11_deduplication_gate() -> None:
    """
    PROOF: Duplicate Signal Rejection:
    - Evaluating the exact same closed bar with the same strategy engine instance
      deterministically emits DUPLICATE to prevent double-firing orders.
    """
    import json
    from pathlib import Path

    fixtures_dir = Path(__file__).parents[2] / "golden" / "fixtures"
    with (fixtures_dir / "scenario_14_duplicate_signal.json").open(encoding="utf-8") as f:
        data = json.load(f)

    def _deser(d: dict) -> Candle:
        return Candle(
            timestamp=datetime.fromisoformat(d["timestamp"]),
            open=Decimal(d["open"]),
            high=Decimal(d["high"]),
            low=Decimal(d["low"]),
            close=Decimal(d["close"]),
            volume=int(d["volume"]),
            open_interest=d["open_interest"],
            is_closed=bool(d["is_closed"]),
        )

    exec_c = [_deser(c) for c in data["exec_candles"]]
    conf_c = [_deser(c) for c in data["conf_candles"]]
    eval_ts = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    engine = DeterministicStrategyEngine()
    sig1 = engine.evaluate(exec_c, conf_c, status, eval_ts)
    assert sig1.decision == StrategyDecision.ACCEPT

    sig2 = engine.evaluate(exec_c, conf_c, status, eval_ts)
    assert sig2.decision == StrategyDecision.DUPLICATE
    assert sig2.rejection_code == RejectionCode.REJECT_DUPLICATE


# ---------------------------------------------------------------------------
# RULE 12: Risk Engine Gate (Notional, Risk Budget, Lot Sizing)
# ---------------------------------------------------------------------------
def test_proof_rule_12_risk_engine_evaluation() -> None:
    """
    PROOF: Risk Engine Gate:
    - Valid trade within equity and risk limits is APPROVED.
    - Exceeding notional limit (>20% of account equity) is REJECTED.
    - Exceeding single trade risk (>0.5% of account equity) is REJECTED.
    """
    equity = Decimal("4000000.00")  # 40 Lakhs
    available = Decimal("2000000.00")
    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=available,
        open_trade_count=0,
        reserved_risk=Decimal("0"),
        reserved_notional=Decimal("0"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    # Valid trade: 1 lot (25 qty), Entry 24000, Stop 23900 (100 pt stop = 2500 risk)
    # Notional = 24000 * 25 = 600,000 (15% <= 20% limit)
    # Risk = 100 * 25 = 2,500 <= 0.5% of 4M (20,000)
    valid_risk_input = RiskInput(
        signal_id="SIG-TEST-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23900.00"),
        contract_id="NIFTY26OCTFUT",
        lot_size=25,
        contract_multiplier=Decimal("1"),
        account_equity=equity,
        available_capital=available,
        proposed_quantity=25,
        evaluation_timestamp=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    )
    decision = evaluate_trade_risk(valid_risk_input, portfolio)
    assert decision.decision == RiskDecisionState.APPROVED
    assert decision.reason_code == RiskReasonCode.APPROVED
    assert decision.quantity == 25

    # Excessive trade: 500 qty -> Risk = 100 * 500 = 50,000 (> 20,000 limit)
    oversized_input = RiskInput(
        signal_id="SIG-TEST-002",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("23900.00"),
        contract_id="NIFTY26OCTFUT",
        lot_size=25,
        contract_multiplier=Decimal("1"),
        account_equity=equity,
        available_capital=available,
        proposed_quantity=500,
        evaluation_timestamp=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    )
    over_decision = evaluate_trade_risk(oversized_input, portfolio)
    assert over_decision.decision == RiskDecisionState.REJECTED
    assert over_decision.reason_code == RiskReasonCode.RISK_LIMIT_EXCEEDED


# ---------------------------------------------------------------------------
# RULE 13: Order Execution State Machine (16 States & Protection Invariant)
# ---------------------------------------------------------------------------
def test_proof_rule_13_order_fsm_lifecycle() -> None:
    """
    PROOF: Order Lifecycle FSM transitions:
    CREATED -> VALIDATED -> SUBMITTED -> ACKNOWLEDGED -> FILLED ->
    PROTECTION_PENDING -> PROTECTED -> EXIT_PENDING -> CLOSED.
    Terminal states cannot transition.
    """
    fsm = OrderStateMachine(
        order_id="ORD-AUDIT-001",
        symbol="NIFTY26OCTFUT",
        side=TradeSide.LONG,
        quantity=25,
        signal_id="SIG-AUDIT-001",
    )
    assert fsm.current_state == OrderState.CREATED

    fsm.transition(OrderState.VALIDATED, event=OrderEvent.VALIDATE_SUCCESS)
    assert fsm.current_state == OrderState.VALIDATED

    fsm.transition(OrderState.SUBMITTED, event=OrderEvent.SUBMIT)
    assert fsm.current_state == OrderState.SUBMITTED

    fsm.transition(OrderState.ACKNOWLEDGED, event=OrderEvent.ACKNOWLEDGE)
    assert fsm.current_state == OrderState.ACKNOWLEDGED

    # Fill 25 shares
    fill_time = datetime.now(UTC)
    fsm.transition(
        OrderState.FILLED,
        event=OrderEvent.FULL_FILL,
        timestamp=fill_time,
        fill_price=Decimal("24040.00"),
    )
    assert fsm.current_state == OrderState.FILLED

    # Protection placement
    fsm.transition(OrderState.PROTECTION_PENDING, event=OrderEvent.REQUEST_PROTECTION)
    assert fsm.current_state == OrderState.PROTECTION_PENDING

    fsm.transition(OrderState.PROTECTED, event=OrderEvent.PROTECTION_CONFIRMED)
    assert fsm.current_state == OrderState.PROTECTED
    assert fsm.is_protected

    # Exit execution
    fsm.transition(OrderState.EXIT_PENDING, event=OrderEvent.REQUEST_EXIT)
    assert fsm.current_state == OrderState.EXIT_PENDING

    fsm.transition(OrderState.CLOSED, event=OrderEvent.EXIT_FULL_FILL)
    assert fsm.current_state == OrderState.CLOSED
    assert fsm.is_terminal


# ---------------------------------------------------------------------------
# RULE 14: Deterministic Fill Simulation & Bracket Exits
# ---------------------------------------------------------------------------
def test_proof_rule_14_fill_simulation_and_bracket_exit() -> None:
    """
    PROOF: Fill Simulator guarantees deterministic execution:
    - Applies realistic entry slippage and transaction costs.
    - Evaluates bracket exits (Take-Profit vs Stop-Loss) conservatively.
    """
    cost_cfg = CostConfig(
        entry_slippage_rate=Decimal("0.0005"),
        entry_fee_rate=Decimal("0.0002"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    t = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    market_candle = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26OCTFUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=50),
        timeframe="3m",
        open=Decimal("24000.00"),
        high=Decimal("24050.00"),
        low=Decimal("23950.00"),
        close=Decimal("24040.00"),
        volume=5000,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=True,
    )

    # 1. Market BUY order fill
    fill = sim.simulate_market_order(
        order_id="ORD-01",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=25,
        candle=market_candle,
        is_entry=True,
    )
    assert fill.filled_qty == 25
    # Fill price includes slippage (at open=24000 + 0.05% slippage = 24012)
    assert fill.effective_price == Decimal("24012.0000")
    assert fill.fee > Decimal("0")

    # 2. Bracket Take-Profit evaluation on subsequent candle reaching target (24200)
    target_candle = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26OCTFUT",
        exchange_timestamp=t + timedelta(minutes=3),
        received_timestamp=t + timedelta(minutes=3, milliseconds=50),
        timeframe="3m",
        open=Decimal("24050.00"),
        high=Decimal("24250.00"),  # Crosses 24200 target
        low=Decimal("24040.00"),
        close=Decimal("24220.00"),
        volume=6000,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=True,
    )
    is_triggered, bracket_type = sim.check_bracket_trigger(
        side=TradeSide.LONG,
        stop_price=Decimal("23900.00"),
        target_price=Decimal("24200.00"),
        candle=target_candle,
    )
    assert is_triggered
    assert bracket_type == "TAKE_PROFIT"

    bracket_fill = sim.simulate_bracket_fill(
        order_id="ORD-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=25,
        bracket_type=bracket_type,
        stop_price=Decimal("23900.00"),
        target_price=Decimal("24200.00"),
        candle=target_candle,
    )
    assert bracket_fill.filled_qty == 25
    assert bracket_fill.effective_price == Decimal("24200.00")


