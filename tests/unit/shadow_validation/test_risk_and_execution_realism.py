"""
Unit Tests for Phase 18-G (Risk Architecture Guardrails) &
Phase 18-H (Execution Simulation Realism).
Tests:
- Data staleness blocking (fail-closed)
- Portfolio heat tracking and single-trade risk bounds
- Intraday drawdown governor circuit breaker
- Volatility-adjusted position sizing
- Gap-through-stop fill pricing (filling at adverse gap open, not stop price)
- Network latency jitter model
- Ack loss and UNKNOWN order state reconciliation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderSide
from alphaforge.shadow_validation.execution_realism import (
    AckLossSimulator,
    GapThroughStopModel,
    LatencyJitterModel,
    OrderAckState,
)
from alphaforge.shadow_validation.risk_guard import InstitutionalRiskGuard


def _make_sample_candle(
    open_p: Decimal, high_p: Decimal, low_p: Decimal, close_p: Decimal
) -> MarketCandle:
    now = datetime(2026, 9, 14, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=now,
        received_timestamp=now,
        timeframe="1m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000,
        source="UPSTOX",
    )


# --- 1. Risk Guardrails ---


def test_stale_data_signal_blocking() -> None:
    guard = InstitutionalRiskGuard(max_data_staleness_seconds=5.0)
    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)

    # Fresh data (2 seconds old) -> Allowed
    fresh, _ = guard.verify_data_freshness(now - timedelta(seconds=2), now)
    assert fresh is True

    # Stale data (6 seconds old) -> Blocked
    stale, reason = guard.verify_data_freshness(now - timedelta(seconds=6), now)
    assert stale is False
    assert "STALE_DATA_BLOCKED" in reason

    # Future data -> Blocked
    future, reason_fut = guard.verify_data_freshness(now + timedelta(seconds=2), now)
    assert future is False
    assert "NEGATIVE_LATENCY" in reason_fut


def test_portfolio_heat_and_single_trade_risk() -> None:
    guard = InstitutionalRiskGuard(
        max_portfolio_heat_pct=Decimal("6.00"),
        max_single_position_risk_pct=Decimal("2.00"),
        initial_capital=Decimal("1000000"),
    )
    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)

    # 1. Trade requesting 25,000 INR risk (2.5% of 1M) -> Exceeds single trade limit (2%)
    ok, reason = guard.authorize_order_risk(
        requested_risk_inr=Decimal("25000"),
        open_positions=[],
        feed_timestamp=now,
        evaluation_timestamp=now,
    )
    assert ok is False
    assert "SINGLE_TRADE_RISK_BREACH" in reason

    # 2. Trade requesting 15,000 INR risk (1.5%) with existing open positions
    # having 50,000 INR risk (5.0%)
    # New heat = 5.0% + 1.5% = 6.5% > 6.0% cap -> Blocked
    positions = [{"reserved_risk_inr": Decimal("50000")}]
    ok_heat, reason_heat = guard.authorize_order_risk(
        requested_risk_inr=Decimal("15000"),
        open_positions=positions,
        feed_timestamp=now,
        evaluation_timestamp=now,
    )
    assert ok_heat is False
    assert "PORTFOLIO_HEAT_BREACH" in reason_heat

    # 3. Trade within limits
    ok_valid, reason_valid = guard.authorize_order_risk(
        requested_risk_inr=Decimal("8000"),
        open_positions=[],
        feed_timestamp=now,
        evaluation_timestamp=now,
    )
    assert ok_valid is True
    assert reason_valid == "RISK_AUTHORIZED"


def test_intraday_drawdown_governor() -> None:
    guard = InstitutionalRiskGuard(
        max_daily_drawdown_pct=Decimal("3.00"),
        initial_capital=Decimal("1000000"),
    )
    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)

    # Equity drops to 980,000 (2% loss) -> Allowed
    guard.update_equity(Decimal("980000"))
    assert guard.daily_lockdown_engaged is False

    # Equity drops to 965,000 (3.5% loss >= 3%) -> Breaches daily limit!
    guard.update_equity(Decimal("965000"))
    assert guard.daily_lockdown_engaged is True
    assert "INTRADAY_DRAWDOWN_BREACH" in (guard.daily_lockdown_reason or "")

    # Any new order request is blocked
    ok, reason = guard.authorize_order_risk(
        requested_risk_inr=Decimal("5000"),
        open_positions=[],
        feed_timestamp=now,
        evaluation_timestamp=now,
    )
    assert ok is False
    assert "INTRADAY_DRAWDOWN_BREACH" in reason


def test_volatility_adjusted_quantity() -> None:
    capital = Decimal("1000000")
    risk_pct = Decimal("1.00")  # 10,000 INR risk budget

    # Case A: Low volatility (ATR = 20)
    qty_low_vol = InstitutionalRiskGuard.calculate_volatility_adjusted_quantity(
        capital=capital,
        risk_pct_per_trade=risk_pct,
        atr=Decimal("20.00"),
        atr_multiplier=Decimal("1.5"),
        lot_size=50,
        max_lots=10,
    )
    # Risk per contract = 20 * 1.5 = 30 -> 10000 / 30 = 333 contracts -> 6 lots = 300 qty
    assert qty_low_vol == 300

    # Case B: High volatility (ATR = 80)
    qty_high_vol = InstitutionalRiskGuard.calculate_volatility_adjusted_quantity(
        capital=capital,
        risk_pct_per_trade=risk_pct,
        atr=Decimal("80.00"),
        atr_multiplier=Decimal("1.5"),
        lot_size=50,
        max_lots=10,
    )
    # Risk per contract = 80 * 1.5 = 120 -> 10000 / 120 = 83 contracts -> 1 lot = 50 qty
    assert qty_high_vol == 50
    assert qty_high_vol < qty_low_vol


# --- 2. Execution Simulation Realism ---


def test_gap_through_stop_pricing() -> None:
    # Stop loss for LONG position at 24,500.00
    stop_p = Decimal("24500.00")

    # Scenario 1: Market GAPS DOWN to open at 24,400.00
    candle_gap = _make_sample_candle(
        open_p=Decimal("24400.00"),
        high_p=Decimal("24420.00"),
        low_p=Decimal("24350.00"),
        close_p=Decimal("24380.00"),
    )

    fill_p, is_gap, slip_amount = GapThroughStopModel.calculate_stop_fill_price(
        side=OrderSide.SELL,
        stop_price=stop_p,
        trigger_candle=candle_gap,
    )
    assert is_gap is True
    # Fill price MUST be at or below gap open 24,400, NEVER at 24,500!
    assert fill_p <= Decimal("24400.00")
    assert slip_amount >= Decimal("100.00")

    # Scenario 2: Normal candle opening at 24,520 and ticking through 24,500
    candle_normal = _make_sample_candle(
        open_p=Decimal("24520.00"),
        high_p=Decimal("24530.00"),
        low_p=Decimal("24480.00"),
        close_p=Decimal("24490.00"),
    )
    fill_normal, is_gap_normal, slip_normal = GapThroughStopModel.calculate_stop_fill_price(
        side=OrderSide.SELL,
        stop_price=stop_p,
        trigger_candle=candle_normal,
    )
    assert is_gap_normal is False
    assert abs(fill_normal - stop_p) < Decimal("10.00")


def test_latency_jitter_model() -> None:
    jitter = LatencyJitterModel(min_latency_ms=10, max_latency_ms=50, seed=77)
    base_ts = datetime(2026, 9, 14, 9, 15, 0, tzinfo=UTC)

    samples = [jitter.sample_latency_ms() for _ in range(50)]
    assert all(10 <= s <= 50 for s in samples)
    # Ensure there is variance (not a constant value)
    assert len(set(samples)) > 5

    delayed_ts = jitter.apply_jitter(base_ts)
    assert delayed_ts > base_ts
    diff_ms = (delayed_ts - base_ts).total_seconds() * 1000
    assert 10 <= diff_ms <= 50


def test_ack_loss_and_reconciliation_cycle() -> None:
    # 100% loss rate for testing UNKNOWN state behavior
    sim = AckLossSimulator(ack_loss_rate=1.0, seed=1)

    state, reason = sim.process_order_submission(
        order_id="ORD-TEST-123",
        symbol="NIFTY26SEPFUT",
        actual_broker_state="FILLED",
    )
    # Order must transition to UNKNOWN, not assumed filled or rejected
    assert state == OrderAckState.UNKNOWN
    assert "NETWORK_TIMEOUT" in reason
    assert "ORD-TEST-123" in sim.in_flight_unknown_orders

    # Explicit reconciliation resolves to ground truth
    ok, recon_msg = sim.reconcile_unknown_order("ORD-TEST-123")
    assert ok is True
    assert "resolved to FILLED" in recon_msg
    assert "ORD-TEST-123" not in sim.in_flight_unknown_orders
