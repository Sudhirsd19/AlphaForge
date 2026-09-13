"""
Unit tests for OBS22: Observability Semantic Equivalence.

Verifies that authoritative trading outcomes under normal observability and
under completely broken/failing observability sinks are 100% BIT-FOR-BIT IDENTICAL.

At minimum compares:
- strategy result
- risk result
- order intent
- client_order_id
- order state
- position result
- reconciliation result
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from alphaforge.observability.events import ObservabilityEvent

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.enums import FuturesConfirmationStatus
from alphaforge.data.normalization import MarketDataNormalizer
from alphaforge.execution.enums import OrderSide, OrderState
from alphaforge.execution.idempotency import OrderRole
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.observability.hub import (
    OrderFSMObservabilityAdapter,
    ReconciliationObservabilityAdapter,
    reset_observability_hub,
    set_global_dispatcher,
)
from alphaforge.observability.sinks import (
    AbstractObservabilitySink,
    InMemoryObservabilitySink,
    SafeObservabilityDispatcher,
)
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.reconciler import ColdBootReconciler
from alphaforge.reconciliation.state_store import InMemoryStateStore
from alphaforge.risk.engine import evaluate_trade_risk
from alphaforge.risk.enums import TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskConfig, RiskInput
from alphaforge.security.authorizer import SecureBroker, SecurityAuthorizer
from alphaforge.security.config import SecurityConfig
from alphaforge.security.credentials import CredentialStore
from alphaforge.security.kill_switch import KillSwitch
from alphaforge.security.startup import SecurityStartupGate
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine

FIXTURES_DIR = Path(__file__).parent.parent.parent / "golden" / "fixtures"


class CrashingSink(AbstractObservabilitySink):
    """Failing sink that raises on every emit, flush, and close."""

    def emit(self, event: ObservabilityEvent) -> None:  # noqa: ARG002
        raise RuntimeError("Severe I/O crash in diagnostic sink")

    def flush(self) -> None:
        raise RuntimeError("Flush crashed")

    def close(self) -> None:
        raise RuntimeError("Close crashed")


def _load_scenario(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        return cast("dict[str, Any]", json.load(f))


def _execute_scenario(is_broken: bool) -> dict[str, Any]:
    """Run full trading pipeline with healthy or broken observability dispatcher."""
    reset_observability_hub()

    sink = CrashingSink() if is_broken else InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])
    set_global_dispatcher(dispatcher)

    try:
        # 1. Normalization
        fixture = _load_scenario("scenario_01_valid_long.json")
        raw_exec: list[dict[str, Any]] = []
        for c in fixture["exec_candles"]:
            ts_str = c["timestamp"].replace("+00:00", "Z")
            raw_exec.append(
                {
                    "symbol": "NIFTY",
                    "instrument_type": "FUTURES",
                    "contract_id": "NIFTY26SEPFUT",
                    "exchange_timestamp": ts_str,
                    "received_timestamp": ts_str,
                    "timeframe": "3m",
                    "open": str(c["open"]),
                    "high": str(c["high"]),
                    "low": str(c["low"]),
                    "close": str(c["close"]),
                    "volume": int(c["volume"]),
                    "open_interest": c["open_interest"],
                    "source": "RAW_FEED",
                    "is_closed": bool(c["is_closed"]),
                }
            )

        normalizer = MarketDataNormalizer(default_timeframe="3m", min_required_candles=20)
        norm_res = normalizer.normalize_batch(raw_exec)

        from alphaforge.data.store import CandleStore

        store = CandleStore()
        store.add_candles(norm_res.valid_candles)

        raw_conf: list[dict[str, Any]] = []
        for c in fixture["conf_candles"]:
            ts_str = c["timestamp"].replace("+00:00", "Z")
            raw_conf.append(
                {
                    "symbol": "NIFTY",
                    "instrument_type": "INDEX",
                    "contract_id": "NIFTY-SPOT",
                    "exchange_timestamp": ts_str,
                    "received_timestamp": ts_str,
                    "timeframe": "15m",
                    "open": str(c["open"]),
                    "high": str(c["high"]),
                    "low": str(c["low"]),
                    "close": str(c["close"]),
                    "volume": int(c["volume"]),
                    "open_interest": c["open_interest"],
                    "source": "RAW_FEED",
                    "is_closed": bool(c["is_closed"]),
                }
            )
        normalizer_15m = MarketDataNormalizer(default_timeframe="15m")
        norm_result_15m = normalizer_15m.normalize_batch(raw_conf, timeframe="15m")
        store.add_candles(norm_result_15m.valid_candles)

        latest_3m = store.get_latest_candle("NIFTY", "3m")
        assert latest_3m is not None
        closed_count = len(fixture["exec_candles"]) - 1
        raw_exec_bridge = store.get_strategy_execution_input("NIFTY", "3m", count=closed_count)
        raw_conf_bridge = store.get_strategy_confirmation_input(
            "NIFTY", "15m", max_timestamp=latest_3m.exchange_timestamp
        )
        assert raw_exec_bridge is not None

        # 2. Strategy
        strat_cfg = StrategyConfig()
        strat_engine = DeterministicStrategyEngine(strat_cfg)
        eval_ts = datetime.fromisoformat(fixture["evaluation_timestamp"])
        signal = strat_engine.evaluate(
            raw_exec_candles=raw_exec_bridge,
            raw_conf_candles=raw_conf_bridge,
            futures_status=FuturesConfirmationStatus.CONFIRMED,
            evaluation_timestamp=eval_ts,
        )

        # 3. Risk
        risk_input = RiskInput(
            signal_id=signal.signal_id.upper(),
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_price=Decimal("24500.00"),
            stop_price=Decimal("24400.00"),
            contract_id="NIFTY26SEPFUT",
            lot_size=50,
            contract_multiplier=Decimal("1"),
            account_equity=Decimal("10000000.00"),
            available_capital=Decimal("10000000.00"),
            proposed_quantity=50,
            evaluation_timestamp=eval_ts,
        )
        port_state = PortfolioRiskState(
            account_equity=Decimal("10000000.00"),
            available_capital=Decimal("10000000.00"),
            daily_starting_equity=Decimal("10000000.00"),
            current_equity=Decimal("10000000.00"),
        )
        risk_res = evaluate_trade_risk(risk_input, port_state, RiskConfig())

        # 4. Security & Broker
        sec_cfg = SecurityConfig()
        ks = KillSwitch()
        recon_gate = ReconciliationGate(initially_open=True)
        startup_gate = SecurityStartupGate(
            security_config=sec_cfg,
            credential_store=CredentialStore(),
            kill_switch=ks,
            reconciliation_gate=recon_gate,
        )
        startup_gate.verify_startup()

        authorizer = SecurityAuthorizer(
            security_config=sec_cfg,
            kill_switch=ks,
            reconciliation_gate=recon_gate,
            startup_gate=startup_gate,
        )
        paper_broker = PaperBroker()
        secure_broker = SecureBroker(paper_broker, authorizer)

        req = BrokerOrderRequest(
            client_order_id="ORD-OBS22-001",
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            order_type=BrokerOrderType.MARKET,
            role=OrderRole.ENTRY,
        )
        order_res = secure_broker.submit_order(req)

        # 5. FSM
        fsm_adapter = OrderFSMObservabilityAdapter(dispatcher)
        fsm = OrderStateMachine(
            order_id="ORD-OBS22-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            signal_id=signal.signal_id.upper(),
            transition_listener=fsm_adapter,
        )
        fsm.transition(OrderState.VALIDATED)
        fsm.transition(OrderState.SUBMITTED)
        fsm.transition(OrderState.ACKNOWLEDGED)
        fsm.transition(OrderState.FILLED, fill_qty=50, fill_price=Decimal("24500.00"))

        # 6. Reconciliation
        recon_adapter = ReconciliationObservabilityAdapter(dispatcher)
        state_store = InMemoryStateStore()
        reconciler = ColdBootReconciler(
            broker=paper_broker,
            state_store=state_store,
            gate=recon_gate,
            reconciliation_listener=recon_adapter,
            on_start_listener=recon_adapter.on_start,
        )
        recon_res = reconciler.reconcile()

        return {
            "norm_valid_count": len(norm_res.valid_candles),
            "norm_execution_allowed": norm_res.execution_allowed,
            "signal_id": signal.signal_id,
            "signal_decision": signal.decision.value,
            "signal_direction": signal.direction.value,
            "signal_entry": str(signal.entry_reference),
            "risk_decision": risk_res.decision.value,
            "risk_reason_code": risk_res.reason_code.value,
            "risk_amount": str(risk_res.risk_amount),
            "client_order_id": order_res.client_order_id,
            "broker_order_id": order_res.broker_order_id,
            "broker_order_status": order_res.status.value,
            "fsm_state": fsm.current_state.value,
            "fsm_filled_qty": fsm.filled_quantity,
            "recon_status": recon_res.status.value,
            "recon_mismatch_count": recon_res.mismatch_count,
            "sink_failure_count": dispatcher.sink_failure_count,
        }
    finally:
        reset_observability_hub()


def test_obs22_observability_semantic_equivalence() -> None:
    """
    OBS22: Compare authoritative trading results under normal vs broken observability.
    Expected: EXACTLY EQUAL across all trading domains.
    """
    out_healthy = _execute_scenario(is_broken=False)
    out_broken = _execute_scenario(is_broken=True)

    # In the broken run, sink failures were caught and counted
    assert out_healthy["sink_failure_count"] == 0
    assert out_broken["sink_failure_count"] > 0

    # Strip diagnostic counter to compare authoritative trading results
    domain_healthy = {k: v for k, v in out_healthy.items() if k != "sink_failure_count"}
    domain_broken = {k: v for k, v in out_broken.items() if k != "sink_failure_count"}

    # Absolute Requirement: Trading results must be 100% BIT-FOR-BIT IDENTICAL
    assert domain_healthy == domain_broken

    # Verify specific core fields explicitly
    assert domain_healthy["signal_decision"] == domain_broken["signal_decision"]
    assert domain_healthy["risk_decision"] == domain_broken["risk_decision"]
    assert domain_healthy["client_order_id"] == domain_broken["client_order_id"]
    assert domain_healthy["fsm_state"] == domain_broken["fsm_state"]
    assert domain_healthy["recon_status"] == domain_broken["recon_status"]
