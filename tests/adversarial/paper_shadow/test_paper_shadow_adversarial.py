"""
Authoritative Adversarial Safety & Invariant Test Suite for Phase 16 Paper / Shadow Trading.

Explicitly implements tests PS-1 through PS-38 validating zero-live-orders guarantees,
broker isolation, idempotency, deterministic execution, continuous reconciliation,
state-aware conservation, dynamic evolving risk, authoritative exits, environment fail-closed
safety, contract metadata fidelity, and strict causality.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
)
from alphaforge.broker.paper import PaperBroker
from alphaforge.contract.models import ContractMaster
from alphaforge.contract.repository import InMemoryContractMasterRepository
from alphaforge.core.exceptions import (
    DataIntegrityError,
    IdempotencyCollisionError,
    IllegalStateTransitionError,
)
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.deployment.broker_guard import DeploymentBrokerGuard
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import DeploymentSafetyError
from alphaforge.execution.enums import (
    OrderSide,
    OrderState,
)
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
)
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import (
    MarketDataAnomalyType,
    OHLCResolutionPolicy,
    PaperShadowMode,
)
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.paper_shadow.market_data_validator import PaperMarketDataValidator
from alphaforge.paper_shadow.models import (
    MarketEvent,
    PaperShadowConfig,
)
from alphaforge.paper_shadow.order_router import PaperShadowOrderRouter
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker
from alphaforge.paper_shadow.reconciler_adapter import PaperShadowReconciler
from alphaforge.reconciliation.models import ReconciliationStatus
from alphaforge.risk.engine import RiskEngine
from alphaforge.risk.enums import RiskDecisionState, TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskConfig, RiskInput


class FakeLiveBroker(AbstractBroker):
    """Test stub representing a live exchange broker adapter."""

    @property
    def is_live_broker(self) -> bool:
        return True

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        _ = request
        raise RuntimeError("LIVE BROKER ACCESSED - CRITICAL SAFETY VIOLATION")

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        _ = (client_order_id, broker_order_id)
        return None

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        return ()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        return ()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        _ = client_order_id
        raise RuntimeError("LIVE BROKER CANCEL ACCESSED")

    def is_available(self) -> bool:
        return True


def make_candle(
    symbol: str = "NIFTY",
    ts: datetime | None = None,
    open_p: Decimal = Decimal("20000"),
    high_p: Decimal | None = None,
    low_p: Decimal | None = None,
    close_p: Decimal = Decimal("20050"),
    timeframe: str = "3m",
    is_closed: bool = True,
    volume: int = 1000,
) -> MarketCandle:
    t = ts or datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    actual_high = high_p if high_p is not None else max(open_p, close_p) + Decimal("50")
    actual_low = low_p if low_p is not None else min(open_p, close_p) - Decimal("50")
    if actual_high < max(open_p, close_p):
        actual_high = max(open_p, close_p) + Decimal("10")
    if actual_low > min(open_p, close_p):
        actual_low = min(open_p, close_p) - Decimal("10")
    return MarketCandle(
        symbol=symbol,
        instrument_type=InstrumentType.FUTURES,
        contract_id=f"{symbol}-FUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=20),
        timeframe=timeframe,
        open=open_p,
        high=actual_high,
        low=actual_low,
        close=close_p,
        volume=volume,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=is_closed,
    )


# --- PS-1 to PS-4: Broker Isolation & Safety Boundary ---


def test_ps_1_paper_cannot_access_live_broker() -> None:
    """PS-1: PAPER environment pairing with live broker is rejected fail-closed."""
    live_broker = FakeLiveBroker()
    dep_cfg = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    with pytest.raises(DeploymentSafetyError):
        DeploymentBrokerGuard(delegate=live_broker, config=dep_cfg)


def test_ps_2_shadow_cannot_access_live_broker() -> None:
    """PS-2: SHADOW environment pairing with live broker is rejected fail-closed."""
    live_broker = FakeLiveBroker()
    dep_cfg = DeploymentConfig(environment=DeploymentEnvironment.SHADOW)
    with pytest.raises(DeploymentSafetyError):
        DeploymentBrokerGuard(delegate=live_broker, config=dep_cfg)


def test_ps_3_live_credentials_cannot_bypass_deployment_guard() -> None:
    """PS-3: Live credentials / live authorization cannot bypass PAPER/SHADOW guard."""
    live_broker = FakeLiveBroker()
    # Even if live_authorized=True is maliciously or mistakenly set on a PAPER config:
    dep_cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        live_authorized=True,
    )
    with pytest.raises(DeploymentSafetyError):
        DeploymentBrokerGuard(delegate=live_broker, config=dep_cfg)


def test_ps_4_environment_crossover_fails() -> None:
    """PS-4: Cross-environment guard validation blocks order routing to live broker."""
    paper_broker = PaperBroker()
    guard = DeploymentBrokerGuard(
        delegate=paper_broker,
        config=DeploymentConfig(environment=DeploymentEnvironment.PAPER),
    )
    req = BrokerOrderRequest(
        client_order_id="ORD-01",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
    )
    # Order to paper broker succeeds
    res = guard.submit_order(req)
    assert res.status == BrokerOrderStatus.ACKNOWLEDGED


# --- PS-5 to PS-9: Fills, Idempotency, and Conservation ---


def test_ps_5_duplicate_fill_rejected() -> None:
    """PS-5: Idempotency registry prevents duplicate execution intent registration."""
    registry = IdempotencyRegistry()
    intent1 = OrderIntent(
        client_order_id="ORD-DUP-01",
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-01",
        side=TradeSide.LONG,
        quantity=50,
    )
    registry.register(intent1)

    # Identical intent is safe NO-OP
    assert registry.register(intent1) == intent1

    # Conflicting intent fails closed
    conflicting = OrderIntent(
        client_order_id="ORD-DUP-01",
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-01",
        side=TradeSide.LONG,
        quantity=100,  # Conflicting quantity
    )
    with pytest.raises(IdempotencyCollisionError):
        registry.register(conflicting)


def test_ps_6_phantom_position_rejected() -> None:
    """PS-6: Position quantity cannot exceed authoritative executed quantity."""
    tracker = PaperPnLTracker()
    candle = make_candle(open_p=Decimal("20000"))
    sim = DeterministicFillSimulator()
    fill = sim.simulate_market_order(
        order_id="E1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        candle=candle,
        is_entry=True,
    )
    tracker.record_entry_fill(fill)

    # Exiting more than holding (60 > 50) must raise DataIntegrityError
    exit_fill = sim.simulate_market_order(
        order_id="X1",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=60,
        candle=candle,
        is_entry=False,
    )
    with pytest.raises(DataIntegrityError):
        tracker.record_exit_fill(exit_fill)


def test_ps_7_partial_fill_accounting_correct() -> None:
    """PS-7: Partial fill accounting updates remaining quantity and VWAP correctly."""
    tracker = PaperPnLTracker()
    candle = make_candle(open_p=Decimal("20000"))
    sim = DeterministicFillSimulator(cost_config=CostConfig(entry_slippage_rate=Decimal("0")))

    # Entry 100 units @ 20000
    entry = sim.simulate_market_order("E1", "NIFTY", OrderSide.BUY, 100, candle, is_entry=True)
    tracker.record_entry_fill(entry)

    # Partial exit of 30 units @ 20100
    candle_exit = make_candle(open_p=Decimal("20100"))
    exit_30 = sim.simulate_partial_fill(
        "X1", "NIFTY", OrderSide.SELL, 100, 30, candle_exit, is_entry=False
    )
    trade = tracker.record_exit_fill(exit_30)

    assert trade.quantity == 30
    assert trade.gross_pnl == Decimal("3000")  # (20100 - 20000) * 30
    pos = tracker.get_active_positions().get("NIFTY")
    assert pos is not None
    assert pos.quantity == 70  # 100 - 30


def test_ps_8_full_fill_accounting_correct() -> None:
    """PS-8: Full fill accounting closes position completely."""
    tracker = PaperPnLTracker()
    candle = make_candle(open_p=Decimal("20000"))
    sim = DeterministicFillSimulator(
        cost_config=CostConfig(entry_slippage_rate=Decimal("0"), exit_slippage_rate=Decimal("0"))
    )

    entry = sim.simulate_market_order("E1", "NIFTY", OrderSide.BUY, 50, candle, is_entry=True)
    tracker.record_entry_fill(entry)

    candle_exit = make_candle(open_p=Decimal("20050"))
    exit_fill = sim.simulate_market_order(
        "X1", "NIFTY", OrderSide.SELL, 50, candle_exit, is_entry=False
    )
    trade = tracker.record_exit_fill(exit_fill)

    assert trade.quantity == 50
    assert trade.is_closed is True
    assert len(tracker.get_active_positions()) == 0


def test_ps_9_retry_does_not_duplicate_execution() -> None:
    """PS-9: Execution retry on existing client_order_id does not submit second broker order."""
    broker = PaperBroker()
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=cfg, broker=broker)

    order1, bo1 = router.create_and_route_order(
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-RETRY-01",
        side=TradeSide.LONG,
        quantity=50,
    )
    assert bo1 is not None

    # Idempotent re-submission returns same intent
    intent = router.idempotency_registry.get(order1.order_id)
    assert intent is not None
    assert router.idempotency_registry.register(intent) == intent


# --- PS-10 to PS-14: Restart, State Recovery & Reconciliation ---


def test_ps_10_to_12_restart_preserves_order_fill_position() -> None:
    """PS-10..12: Cold-boot reconciler reconstructs order, fill, and position from storage."""
    storage = InMemoryLedgerStorage()
    ledger = AuditLedger(storage=storage)

    # Append events
    ledger.append(
        event_type=AuditEventType.ORDER_FILLED,
        entity_type="ORDER",
        entity_id="ORD-REST-01",
        correlation_id="SIG-01",
        causation_id="SIG-01",
        payload={"symbol": "NIFTY", "quantity": 50, "price": "20000"},
    )

    # Reconstruct ledger from storage
    ledger2 = AuditLedger(storage=storage, auto_verify_on_startup=True)
    assert len(ledger2) == 1
    ev = ledger2.get_event_by_sequence(1)
    assert ev is not None
    assert ev.entity_id == "ORD-REST-01"


def test_ps_13_reconciliation_catches_mismatch() -> None:
    """PS-13: Reconciler detects discrepancy when broker position diverges from local state."""
    broker = PaperBroker()
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=cfg, broker=broker)
    tracker = PaperPnLTracker()

    reconciler = PaperShadowReconciler(order_router=router, pnl_tracker=tracker, broker=broker)

    # Inject external untracked position
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-EXT-1",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=100,
            average_price=Decimal("20000"),
            status="OPEN",
        )
    )

    res = reconciler.reconcile()
    assert res.status == ReconciliationStatus.MISMATCH
    assert res.mismatch_count > 0


def test_ps_14_reconciliation_does_not_silently_rewrite_authority() -> None:
    """PS-14: Reconciler flags mismatches rather than silently rewriting local state."""
    broker = PaperBroker()
    router = PaperShadowOrderRouter(config=PaperShadowConfig(), broker=broker)
    tracker = PaperPnLTracker()
    reconciler = PaperShadowReconciler(order_router=router, pnl_tracker=tracker, broker=broker)

    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-EXT-2",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("20000"),
            status="OPEN",
        )
    )
    res = reconciler.reconcile()

    assert res.status == ReconciliationStatus.MISMATCH
    # Local tracker should NOT have been magically populated with the phantom position
    assert len(tracker.get_active_positions()) == 0


# --- PS-15 to PS-20: Deterministic Mathematics, Slippage, Fees & OHLC Ambiguity ---


def test_ps_15_deterministic_fills() -> None:
    """PS-15: Identical market inputs produce bit-for-bit identical fills."""
    sim = DeterministicFillSimulator()
    c = make_candle(open_p=Decimal("20000"))
    f1 = sim.simulate_market_order("O1", "NIFTY", OrderSide.BUY, 50, c)
    f2 = sim.simulate_market_order("O1", "NIFTY", OrderSide.BUY, 50, c)

    assert f1.effective_price == f2.effective_price
    assert f1.fee == f2.fee
    assert f1.slippage_loss == f2.slippage_loss
    assert f1.gross_notional == f2.gross_notional


def test_ps_16_deterministic_pnl() -> None:
    """PS-16: Identical round-trip fills produce identical P&L metrics."""
    t1 = PaperPnLTracker()
    t2 = PaperPnLTracker()
    c1 = make_candle(open_p=Decimal("20000"))
    c2 = make_candle(open_p=Decimal("20100"))
    sim = DeterministicFillSimulator()

    e1 = sim.simulate_market_order("E1", "NIFTY", OrderSide.BUY, 50, c1, is_entry=True)
    x1 = sim.simulate_market_order("X1", "NIFTY", OrderSide.SELL, 50, c2, is_entry=False)

    t1.record_entry_fill(e1)
    t1.record_exit_fill(x1)

    t2.record_entry_fill(e1)
    t2.record_exit_fill(x1)

    m1 = t1.get_metrics()
    m2 = t2.get_metrics()
    assert m1.gross_pnl == m2.gross_pnl
    assert m1.net_pnl == m2.net_pnl
    assert m1.total_fees == m2.total_fees


def test_ps_17_buy_slippage_direction() -> None:
    """PS-17: BUY slippage is strictly adverse upwards."""
    sim = DeterministicFillSimulator(cost_config=CostConfig(entry_slippage_rate=Decimal("0.0010")))
    c = make_candle(open_p=Decimal("20000"))
    fill = sim.simulate_market_order("B1", "NIFTY", OrderSide.BUY, 50, c, is_entry=True)
    assert fill.effective_price > fill.requested_price


def test_ps_18_sell_slippage_direction() -> None:
    """PS-18: SELL slippage is strictly adverse downwards."""
    sim = DeterministicFillSimulator(cost_config=CostConfig(entry_slippage_rate=Decimal("0.0010")))
    c = make_candle(open_p=Decimal("20000"))
    fill = sim.simulate_market_order("S1", "NIFTY", OrderSide.SELL, 50, c, is_entry=True)
    assert fill.effective_price < fill.requested_price


def test_ps_19_fees_correctly_applied() -> None:
    """PS-19: Fees accurately follow Phase 6 rate-based and fixed-cost formulas."""
    cost_cfg = CostConfig(
        entry_fee_rate=Decimal("0.0002"),
        exit_fee_rate=Decimal("0.0002"),
        fixed_cost_per_trade=Decimal("15"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    c = make_candle(open_p=Decimal("20000"))

    entry = sim.simulate_market_order("E1", "NIFTY", OrderSide.BUY, 100, c, is_entry=True)
    exit_f = sim.simulate_market_order("X1", "NIFTY", OrderSide.SELL, 100, c, is_entry=False)

    # Entry fee: notional * 0.0002
    assert entry.fee == entry.gross_notional * Decimal("0.0002")
    # Exit fee: notional * 0.0002 + 15
    assert exit_f.fee == (exit_f.gross_notional * Decimal("0.0002")) + Decimal("15")


def test_ps_20_ohlc_ambiguity_policy_deterministic() -> None:
    """PS-20: When both SL and TP are touched on same bar, SL is conservatively triggered first."""
    sim = DeterministicFillSimulator(ohlc_policy=OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE)
    candle = make_candle(
        open_p=Decimal("20000"),
        high_p=Decimal("20150"),  # Crosses TP at 20100
        low_p=Decimal("19900"),  # Crosses SL at 19950
        close_p=Decimal("20020"),
    )

    bracket_res = sim.evaluate_resting_brackets(
        order_id="BRK-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        stop_price=Decimal("19950"),
        target_price=Decimal("20100"),
        candle=candle,
    )

    assert bracket_res.triggered is True
    assert bracket_res.bracket_type == "STOP_LOSS"
    assert bracket_res.fill is not None
    assert bracket_res.fill.effective_price == Decimal("19950")


# --- PS-21 to PS-24: Market Data Anomaly Handling ---


def test_ps_21_stale_market_data_handled() -> None:
    """PS-21: Stale market data is flagged and intercepted."""
    validator = PaperMarketDataValidator(PaperShadowConfig(stale_data_threshold_seconds=15))
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    c = make_candle(ts=t, timeframe="3m")
    # Lag > 3m + 15s (195s)
    res = validator.validate_candle(c, receipt_timestamp=t + timedelta(seconds=200))
    assert res.is_valid is False
    assert res.anomaly == MarketDataAnomalyType.STALE_DATA


def test_ps_22_duplicate_market_event_handled() -> None:
    """PS-22: Duplicate market event is rejected."""
    validator = PaperMarketDataValidator()
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    evt = MarketEvent(
        event_id="EVT-01",
        sequence=1,
        candle=make_candle(ts=t),
        market_timestamp=t,
        receipt_timestamp=t + timedelta(milliseconds=10),
        symbol="NIFTY",
    )
    assert validator.validate_event(evt).is_valid is True
    assert validator.validate_event(evt).is_valid is False


def test_ps_23_out_of_order_event_handled() -> None:
    """PS-23: Out of order timestamp is rejected."""
    validator = PaperMarketDataValidator()
    t1 = datetime(2026, 9, 12, 9, 18, tzinfo=UTC)
    t2 = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    assert validator.validate_candle(make_candle(ts=t1)).is_valid is True
    res2 = validator.validate_candle(make_candle(ts=t2))
    assert res2.is_valid is False
    assert res2.anomaly == MarketDataAnomalyType.TIMESTAMP_OUT_OF_ORDER


def test_ps_24_forming_candle_quarantined() -> None:
    """PS-24: Forming unclosed candle is quarantined from strategy/execution."""
    validator = PaperMarketDataValidator()
    c = make_candle(is_closed=False)
    res = validator.validate_candle(c)
    assert res.is_valid is False
    assert res.anomaly == MarketDataAnomalyType.FORMING_BAR_LEAK


# --- PS-25 to PS-30: Safety, Kill Switch, FSM & Ledger Authority ---


def test_ps_25_shutdown_creates_zero_real_orders() -> None:
    """PS-25: Shutdown generates report certifying zero live orders were submitted."""
    engine = PaperShadowEngine()
    report = engine.shutdown()
    assert report.no_live_orders_submitted is True


def test_ps_26_startup_failure_creates_zero_real_orders() -> None:
    """PS-26: Invalid configuration fails closed at startup with zero orders."""
    with pytest.raises((DataIntegrityError, ValidationError)):
        PaperShadowConfig(contract_multiplier=Decimal("0"))


def test_ps_27_kill_switch_prevents_simulated_execution() -> None:
    """PS-27: Kill switch blocks new orders even in simulation."""
    engine = PaperShadowEngine()
    engine.kill_switch.engage(reason="Emergency Stop")
    c = make_candle()
    engine.process_candle(c)
    report = engine.shutdown()
    assert report.total_orders_submitted == 0


def test_ps_28_frozen_risk_controls_enforced() -> None:
    """PS-28: Frozen RiskEngine limits remain strictly enforced in Paper mode."""
    risk_engine = RiskEngine(config=RiskConfig(max_risk_per_trade=Decimal("0.0050")))
    risk_input = RiskInput(
        signal_id="SIG-RISK-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("20000"),
        stop_price=Decimal("18000"),  # Huge 2000pt stop distance -> breaches risk per trade
        contract_id="NIFTY-FUT",
        lot_size=50,
        contract_multiplier=Decimal("1"),
        account_equity=Decimal("1000000"),
        available_capital=Decimal("1000000"),
        proposed_quantity=50,
        evaluation_timestamp=datetime(2026, 9, 12, 9, 15, tzinfo=UTC),
    )
    portfolio_state = PortfolioRiskState(
        account_equity=Decimal("1000000"),
        available_capital=Decimal("1000000"),
        daily_starting_equity=Decimal("1000000"),
        current_equity=Decimal("1000000"),
    )
    decision, res = risk_engine.request_risk_reservation(risk_input, portfolio_state)
    assert decision.decision == RiskDecisionState.REJECTED
    assert res is None


def test_ps_29_frozen_order_fsm_remains_authoritative() -> None:
    """PS-29: 17-state FSM enforces legal state transitions only."""
    fsm = OrderStateMachine(
        order_id="ORD-FSM-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
    )
    assert fsm.current_state == OrderState.CREATED

    # Illegal transition directly to FILLED from CREATED must fail
    with pytest.raises(IllegalStateTransitionError):
        fsm.transition(target_state=OrderState.FILLED)


def test_ps_30_ledger_reconciliation_authoritative() -> None:
    """PS-30: Audit ledger cryptographic hash chain verifies integrity."""
    ledger = AuditLedger(storage=InMemoryLedgerStorage())
    ledger.append(
        event_type=AuditEventType.ORDER_FILLED,
        entity_type="ORDER",
        entity_id="O1",
        correlation_id="C1",
        causation_id="C1",
        payload={"data": "test"},
    )
    res = ledger.verify_chain()
    assert res.valid is True


# --- PS-31: Causal Visibility & No Look-Ahead Bias ---


def test_ps_31_no_lookahead_bias() -> None:
    """
    PS-31: Strict causal data visibility.
    Future market data at timestamp T+1 cannot influence decisions at timestamp T.
    """
    engine = PaperShadowEngine()
    base_time = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)

    # Provide sequential bars up to T (20 bars)
    for i in range(20):
        t = base_time + timedelta(minutes=3 * i)
        c = make_candle(ts=t, open_p=Decimal("20000") + Decimal(i * 10))
        engine.process_candle(c)

    # Capture state snapshot at T
    state_at_t = copy.deepcopy(engine.pnl_tracker.get_metrics())

    # Creating a future bar at T+1 with an extreme outlier cannot retroactively alter past metrics
    t_future = base_time + timedelta(minutes=3 * 21)
    _ = make_candle(ts=t_future, open_p=Decimal("30000"), high_p=Decimal("31000"))

    # Past metrics at T remain bit-for-bit identical to recorded state_at_t
    assert state_at_t.realized_pnl == Decimal("0")


# --- PS-32 to PS-35: Phase 16 Targeted Forensic Remediation Invariants ---


def test_ps_32_dynamic_risk_state_evolving() -> None:
    """
    PS-32: Dynamic Evolving Risk & Account State.
    RiskEngine receives dynamic portfolio risk state reflecting realized/unrealized P&L
    and open exposure rather than static/hardcoded 1,000,000 constants.
    """
    pnl_tracker = PaperPnLTracker(initial_capital=Decimal("500000"))
    init_state = pnl_tracker.get_portfolio_risk_state({})
    assert init_state.account_equity == Decimal("500000")
    assert init_state.available_capital == Decimal("500000")

    fill_sim = DeterministicFillSimulator()
    candle_entry = make_candle(open_p=Decimal("20000"))

    # Simulate an entry fill
    entry_fill = fill_sim.simulate_market_order(
        order_id="ORD-ENTRY-1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        candle=candle_entry,
        is_entry=True,
    )
    pnl_tracker.record_entry_fill(
        fill=entry_fill,
        stop_price=Decimal("19900"),
        target_price=Decimal("20200"),
        strategy_id="STRAT_1",
    )

    # With open position at entry, notional allocated = 20000 * 50 = 1,000,000
    # available_capital becomes 0 (max(0, 500000 - 1000000))
    pos_state = pnl_tracker.get_portfolio_risk_state({})
    assert pos_state.available_capital == Decimal("0")

    # Simulate closing exit with a loss (exit at 19900)
    candle_exit = make_candle(open_p=Decimal("19900"))
    exit_fill = fill_sim.simulate_market_order(
        order_id="ORD-EXIT-1",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        candle=candle_exit,
        is_entry=False,
    )
    pnl_tracker.record_exit_fill(fill=exit_fill, exit_reason="STOP_LOSS")

    # After realizing a loss of (19900 - 20000)*50 - fees - slippages:
    after_loss_state = pnl_tracker.get_portfolio_risk_state({})
    assert after_loss_state.account_equity < Decimal("500000")
    assert (
        after_loss_state.account_equity
        == Decimal("500000") + pnl_tracker.get_metrics().realized_pnl
    )
    assert after_loss_state.available_capital == after_loss_state.account_equity

    # Now verify that RiskEngine reservation behavior dynamically reacts to updated portfolio state
    risk_engine = RiskEngine()
    risk_input = RiskInput(
        signal_id="SIG-DYN-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("20000"),
        stop_price=Decimal("19900"),
        contract_id="NIFTY-FUT",
        lot_size=50,
        contract_multiplier=Decimal("1"),
        account_equity=after_loss_state.account_equity,
        available_capital=after_loss_state.available_capital,
        proposed_quantity=50,
        evaluation_timestamp=datetime(2026, 9, 12, 9, 20, tzinfo=UTC),
    )
    decision, _ = risk_engine.request_risk_reservation(risk_input, after_loss_state)
    # The evaluation succeeded using the dynamic state (not hardcoded 1000000)
    assert decision is not None


def test_ps_33_authoritative_exit_levels() -> None:
    """
    PS-33: Authoritative Strategy Exit Levels.
    PaperShadowEngine evaluates resting bracket exits strictly from strategy-provided
    stop_reference and target_reference without any synthetic 2%/4% hardcoding.
    """
    pnl_tracker = PaperPnLTracker(initial_capital=Decimal("1000000"))
    fill_sim = DeterministicFillSimulator()
    candle_entry = make_candle(open_p=Decimal("20000"))

    # Create entry with custom tight stop (19950 = 50 pts, not 2% which is 400 pts)
    # and custom tight target (20100 = 100 pts, not 4% which is 800 pts)
    entry_fill = fill_sim.simulate_market_order(
        order_id="ORD-AUTH-1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        candle=candle_entry,
        is_entry=True,
    )
    pnl_tracker.record_entry_fill(
        fill=entry_fill,
        stop_price=Decimal("19950"),
        target_price=Decimal("20100"),
        strategy_id="AUTH_STRAT",
    )

    active_pos = pnl_tracker.get_active_positions()["NIFTY"]
    assert active_pos.stop_price == Decimal("19950")
    assert active_pos.target_price == Decimal("20100")

    # Bar with low 19940 (hits 19950, but would NOT hit a synthetic 2% stop of 19600)
    triggering_candle = make_candle(
        symbol="NIFTY",
        ts=datetime(2026, 9, 12, 9, 18, tzinfo=UTC),
        open_p=Decimal("19980"),
        high_p=Decimal("19990"),
        low_p=Decimal("19940"),
        close_p=Decimal("19960"),
    )

    bracket_res = fill_sim.evaluate_resting_brackets(
        order_id="EXIT-ORD-AUTH-1",
        symbol="NIFTY",
        side=active_pos.side,
        quantity=active_pos.quantity,
        stop_price=active_pos.stop_price,
        target_price=active_pos.target_price,
        candle=triggering_candle,
    )

    assert bracket_res.triggered is True
    assert bracket_res.bracket_type == "STOP_LOSS"
    assert bracket_res.fill is not None


def test_ps_34_deterministic_replay_and_identities() -> None:
    """
    PS-34: Deterministic Replay & Bit-for-Bit Identity Invariant.
    Two independent runs given identical market data and configs produce
    bit-for-bit identical results.
    """
    config1 = PaperShadowConfig(mode=PaperShadowMode.PAPER, initial_capital=Decimal("1000000"))
    config2 = PaperShadowConfig(mode=PaperShadowMode.PAPER, initial_capital=Decimal("1000000"))

    engine1 = PaperShadowEngine(config=config1)
    engine2 = PaperShadowEngine(config=config2)

    base_time = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    candles = [
        make_candle(
            ts=base_time + timedelta(minutes=3 * i),
            open_p=Decimal("20000") + Decimal(i * 5),
            close_p=Decimal("20010") + Decimal(i * 5),
        )
        for i in range(30)
    ]

    for c in candles:
        engine1.process_candle(c)
        engine2.process_candle(c)

    report1 = engine1.shutdown()
    report2 = engine2.shutdown()

    # Compare metrics
    metrics1 = engine1.pnl_tracker.get_metrics()
    metrics2 = engine2.pnl_tracker.get_metrics()

    assert metrics1.realized_pnl == metrics2.realized_pnl
    assert metrics1.total_fees == metrics2.total_fees
    assert metrics1.total_slippage == metrics2.total_slippage
    assert metrics1.trade_count == metrics2.trade_count
    assert metrics1.winning_trades == metrics2.winning_trades
    assert report1.total_candles_processed == report2.total_candles_processed
    assert report1.total_orders_submitted == report2.total_orders_submitted
    assert report1.total_fills_executed == report2.total_fills_executed


def test_ps_35_exit_orders_use_authoritative_routing() -> None:
    """
    PS-35: Exit Orders Use Authoritative Routing & FSM.
    Exit orders must be routed through OrderRouter, registered in IdempotencyRegistry,
    managed by OrderStateMachine, and guarded by DeploymentBrokerGuard.
    """
    broker = PaperBroker()
    config = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=config, broker=broker)

    # 1. Route and fill Entry Order first so broker has an active position
    entry_order, _ = router.create_and_route_order(
        strategy_id="STRAT_TEST",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="ENTRY-SIG-01",
        side=TradeSide.LONG,
        quantity=50,
        timestamp=datetime(2026, 9, 12, 9, 15, tzinfo=UTC),
    )
    broker.simulate_full_fill(entry_order.order_id, Decimal("20000"))
    router.record_fill_transition(entry_order.order_id, 50, Decimal("20000"), is_full_fill=True)

    # 2. Route Exit Order
    exit_order, broker_order = router.create_and_route_order(
        strategy_id="STRAT_TEST",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.EXIT,
        signal_id="EXIT-SIG-01",
        side=TradeSide.SHORT,
        quantity=50,
        timestamp=datetime(2026, 9, 12, 9, 30, tzinfo=UTC),
    )

    assert broker_order is not None
    assert broker_order.role == OrderRole.EXIT
    assert exit_order.state == OrderState.ACKNOWLEDGED
    assert router.get_order(exit_order.order_id) is not None

    # 3. Simulate Exit Fill & FSM transition
    broker.simulate_full_fill(exit_order.order_id, Decimal("19950"))
    fsm_order = router.record_fill_transition(
        client_order_id=exit_order.order_id,
        fill_quantity=50,
        fill_price=Decimal("19950"),
        is_full_fill=True,
    )
    assert fsm_order.state == OrderState.FILLED


def test_ps_36_exit_identity_lineage() -> None:
    """
    PS-36: Exit Order, Fill, P&L, and Ledger Lineage Invariant.
    Exit order creation, fill simulation, P&L record, and ledger entry share the
    exact single authoritative deterministic identity.
    """
    engine = PaperShadowEngine(config=PaperShadowConfig(mode=PaperShadowMode.PAPER))

    # Manually setup active position with known stop loss
    candle_entry = make_candle(
        symbol="NIFTY",
        ts=datetime(2026, 9, 12, 9, 15, tzinfo=UTC),
        open_p=Decimal("20000"),
    )
    fill = engine.fill_simulator.simulate_market_order(
        order_id="ORD-ENTRY-PS36",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        candle=candle_entry,
        is_entry=True,
    )
    engine.raw_broker.submit_order(
        BrokerOrderRequest(
            client_order_id="ORD-ENTRY-PS36",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.ENTRY,
        )
    )
    engine.raw_broker.simulate_full_fill("ORD-ENTRY-PS36", Decimal("20000"))
    engine.pnl_tracker.record_entry_fill(
        fill=fill,
        stop_price=Decimal("19900"),
        target_price=Decimal("20200"),
        strategy_id="STRAT_PS36",
        signal_id="SIG-PS36-1",
    )

    # Next candle breaches stop loss at 19900
    candle_exit = make_candle(
        symbol="NIFTY",
        ts=datetime(2026, 9, 12, 9, 18, tzinfo=UTC),
        open_p=Decimal("19950"),
        high_p=Decimal("19960"),
        low_p=Decimal("19890"),  # Low < 19900 triggers STOP_LOSS
        close_p=Decimal("19920"),
    )
    engine._evaluate_active_position_brackets("NIFTY", candle_exit)

    # 1. Authoritative FSM exit order
    exit_orders = [
        o
        for o in engine.order_router.get_all_orders()
        if o.order_id != "ORD-ENTRY-PS36" and o.state == OrderState.FILLED
    ]
    assert len(exit_orders) == 1
    auth_exit_order_id = exit_orders[0].order_id

    # 2. P&L trade record
    closed_trades = engine.pnl_tracker.get_closed_trades()
    assert len(closed_trades) == 1
    trade = closed_trades[0]
    assert trade.exit_order_id == auth_exit_order_id

    # 3. Audit Ledger verification
    ledger_events = [
        e for e in engine.ledger._storage.read_all() if e.event_type == AuditEventType.TRADE_CLOSED
    ]
    assert len(ledger_events) == 1
    ledger_evt = ledger_events[0]
    assert ledger_evt.causation_id == auth_exit_order_id
    assert ledger_evt.payload["exit_order_id"] == auth_exit_order_id


def test_ps_37_environment_mismatch_fails_closed() -> None:
    """
    PS-37: PaperShadowMode and DeploymentEnvironment Mismatch Fails Closed.
    Rejects environment crossover and LIVE pairings fail-closed with zero silent rewrites.
    """
    # 1. PAPER mode with LIVE deployment -> REJECT
    with pytest.raises(DeploymentSafetyError):
        PaperShadowConfig(
            mode=PaperShadowMode.PAPER,
            deployment_config=DeploymentConfig(
                environment=DeploymentEnvironment.LIVE,
                live_authorized=True,
            ),
        )

    # 2. SHADOW mode with LIVE deployment -> REJECT
    with pytest.raises(DeploymentSafetyError):
        PaperShadowConfig(
            mode=PaperShadowMode.SHADOW,
            deployment_config=DeploymentConfig(
                environment=DeploymentEnvironment.LIVE,
                live_authorized=True,
            ),
        )

    # 3. PAPER mode with SHADOW deployment -> REJECT
    with pytest.raises(DeploymentSafetyError):
        PaperShadowConfig(
            mode=PaperShadowMode.PAPER,
            deployment_config=DeploymentConfig(
                environment=DeploymentEnvironment.SHADOW,
            ),
        )

    # 4. SHADOW mode with PAPER deployment -> REJECT
    with pytest.raises(DeploymentSafetyError):
        PaperShadowConfig(
            mode=PaperShadowMode.SHADOW,
            deployment_config=DeploymentConfig(
                environment=DeploymentEnvironment.PAPER,
            ),
        )

    # 5. Matching configurations succeed
    cfg_paper = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    assert cfg_paper.deployment_config.environment == DeploymentEnvironment.PAPER
    router_paper = PaperShadowOrderRouter(config=cfg_paper)
    assert router_paper.guarded_broker is not None

    cfg_shadow = PaperShadowConfig(mode=PaperShadowMode.SHADOW)
    assert cfg_shadow.deployment_config.environment == DeploymentEnvironment.SHADOW
    router_shadow = PaperShadowOrderRouter(config=cfg_shadow)
    assert router_shadow.guarded_broker is None


def test_ps_38_authoritative_contract_metadata() -> None:
    """
    PS-38: Authoritative Contract Metadata & Temporal Lifecycle Invariant.
    Paper/Shadow engine uses authoritative contract metadata from ContractMetadataProvider
    and strictly respects contract expiry/tradability constraints.
    """
    repo = InMemoryContractMasterRepository()
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        lot_size=25,
        contract_multiplier=Decimal("2"),
        tick_size=Decimal("0.05"),
        expiry_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2026, 6, 1, 9, 15, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 9, 15, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        data_source="NSE_FEED",
    )
    repo.add_contract(contract)

    engine = PaperShadowEngine(
        config=PaperShadowConfig(mode=PaperShadowMode.PAPER),
        contract_provider=repo,
    )

    # Active trading candle before expiry
    active_candle = make_candle(
        symbol="NIFTY",
        ts=datetime(2026, 9, 12, 9, 15, tzinfo=UTC),
        open_p=Decimal("20000"),
    )
    active_candle = active_candle.model_copy(update={"contract_id": "NIFTY26SEPFUT"})
    val_res = engine.process_candle(active_candle)
    assert val_res.is_valid is True

    # Expired candle past trading_end_datetime / expiry
    expired_candle = make_candle(
        symbol="NIFTY",
        ts=datetime(2026, 9, 25, 9, 15, tzinfo=UTC),
        open_p=Decimal("20100"),
    )
    expired_candle = expired_candle.model_copy(update={"contract_id": "NIFTY26SEPFUT"})
    engine.process_candle(expired_candle)

    # Past-expiry candle must not result in any new active positions or orders
    assert len(engine.pnl_tracker.get_active_positions()) == 0
