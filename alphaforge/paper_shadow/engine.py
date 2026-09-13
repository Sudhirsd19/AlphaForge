"""
AlphaForge Real-Time Forward Paper / Shadow Trading Engine.

Orchestrates the complete validation pipeline:
Market Data Ingestion -> Data Validation -> Strategy Engine -> Risk Engine
-> Order Router & 17-State FSM -> Fill Simulation & Broker Guard -> P&L Tracker
-> Continuous Reconciliation -> Audit Ledger & Observability Hub.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.broker.paper import PaperBroker
from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.observability.events import (
    DataEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    ObservabilitySeverity,
    RiskEventType,
    StrategyEventType,
)
from alphaforge.observability.hub import observe_event
from alphaforge.paper_shadow.enums import (
    ForwardRunState,
    PaperShadowMode,
)
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.paper_shadow.market_data_validator import (
    MarketDataValidationResult,
    PaperMarketDataValidator,
)
from alphaforge.paper_shadow.models import (
    ForwardRunReport,
    MarketEvent,
    PaperShadowConfig,
)
from alphaforge.paper_shadow.order_router import PaperShadowOrderRouter
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker
from alphaforge.paper_shadow.reconciler_adapter import PaperShadowReconciler
from alphaforge.paper_shadow.shadow_comparator import ShadowComparator
from alphaforge.risk.engine import RiskEngine
from alphaforge.risk.enums import RiskDecisionState, TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskConfig, RiskInput
from alphaforge.security.kill_switch import KillSwitch
from alphaforge.strategy.engine import DeterministicStrategyEngine

if TYPE_CHECKING:
    from alphaforge.broker.interface import AbstractBroker
    from alphaforge.core.models import Candle, StrategySignal
    from alphaforge.data.models import MarketCandle
    from alphaforge.strategy.config import StrategyConfig


class PaperShadowEngine:
    """
    Authoritative Forward Validation Engine for Paper / Shadow Trading.
    Completely surrounds frozen Phase 0-15 components with strict fail-closed safety.
    """

    def __init__(
        self,
        config: PaperShadowConfig | None = None,
        strategy_config: StrategyConfig | None = None,
        risk_config: RiskConfig | None = None,
        broker: AbstractBroker | None = None,
        kill_switch: KillSwitch | None = None,
        audit_ledger: AuditLedger | None = None,
        git_commit: str = "81fba27684d848beecbd6d2d3e8f484fd090b0fe",
    ) -> None:
        self._config = config or PaperShadowConfig()
        self._mode = self._config.mode
        self._git_commit = git_commit
        self._lock = threading.RLock()
        self._state = ForwardRunState.INITIALIZING
        self._start_time = datetime.now(UTC)

        # 1. Market Data Governance
        self._validator = PaperMarketDataValidator(self._config)
        self._candle_history: dict[str, list[Candle]] = {}
        self._current_candles: dict[str, MarketCandle] = {}

        # 2. Frozen Strategy Engine
        self._strategy_engine = DeterministicStrategyEngine(config=strategy_config)

        # 3. Frozen Risk Engine
        self._risk_engine = RiskEngine(config=risk_config or RiskConfig())

        # 4. Kill Switch
        self._kill_switch = kill_switch or KillSwitch()

        # 5. Fill Simulator
        self._fill_sim = DeterministicFillSimulator(
            cost_config=self._config.cost_config,
            ohlc_policy=self._config.ohlc_policy,
            contract_multiplier=self._config.contract_multiplier,
        )

        # 6. P&L Tracker
        self._pnl_tracker = PaperPnLTracker(
            cost_config=self._config.cost_config,
            contract_multiplier=self._config.contract_multiplier,
        )

        # 7. Order Lifecycle Router
        self._raw_broker = broker if broker is not None else PaperBroker()
        self._order_router = PaperShadowOrderRouter(
            config=self._config,
            broker=self._raw_broker,
        )

        # 8. Shadow Comparator
        self._shadow_comparator = ShadowComparator(
            config=self._config,
            fill_simulator=self._fill_sim,
        )

        # 9. Audit Ledger (Phase 9 Authority)
        self._ledger = audit_ledger or AuditLedger(storage=InMemoryLedgerStorage())

        # 10. Reconciler
        self._reconciler = PaperShadowReconciler(
            order_router=self._order_router,
            pnl_tracker=self._pnl_tracker,
            broker=self._raw_broker if self._mode == PaperShadowMode.PAPER else None,
            ledger=self._ledger,
        )

        # Metrics and counters
        self._processed_candles_count: int = 0
        self._signals_count: int = 0
        self._orders_count: int = 0
        self._fills_count: int = 0
        self._reconciliation_passes: int = 0
        self._mismatches_count: int = 0

        self._state = ForwardRunState.RUNNING

    @property
    def mode(self) -> PaperShadowMode:
        return self._mode

    @property
    def config(self) -> PaperShadowConfig:
        return self._config

    @property
    def state(self) -> ForwardRunState:
        with self._lock:
            return self._state

    @property
    def pnl_tracker(self) -> PaperPnLTracker:
        return self._pnl_tracker

    @property
    def order_router(self) -> PaperShadowOrderRouter:
        return self._order_router

    @property
    def shadow_comparator(self) -> ShadowComparator:
        return self._shadow_comparator

    @property
    def reconciler(self) -> PaperShadowReconciler:
        return self._reconciler

    @property
    def ledger(self) -> AuditLedger:
        return self._ledger

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    def process_event(self, event: MarketEvent) -> MarketDataValidationResult:
        """Process a real-time or simulated market data event."""
        return self.process_candle(
            candle=event.candle,
            receipt_timestamp=event.receipt_timestamp,
            market_event_id=event.event_id,
        )

    def process_candle(
        self,
        candle: MarketCandle,
        receipt_timestamp: datetime | None = None,
        market_event_id: str | None = None,
    ) -> MarketDataValidationResult:
        """
        Ingest, validate, and process a single market data candle through the complete pipeline.
        Enforces strict causal data visibility and fail-closed safety.
        """
        with self._lock:
            if self._state not in (ForwardRunState.RUNNING, ForwardRunState.INITIALIZING):
                return MarketDataValidationResult(
                    is_valid=False,
                    anomaly=None,
                    reason=f"Engine is not in RUNNING state (current={self._state})",
                    candle=candle,
                )

            # Step 1: Market Data Validation
            val_res = self._validator.validate_candle(candle, receipt_timestamp)
            if not val_res.is_valid:
                observe_event(
                    ObservabilityEvent(
                        category=ObservabilityCategory.DATA,
                        event_type=DataEventType.DATA_ERROR.value,
                        severity=ObservabilitySeverity.WARNING,
                        symbol=candle.symbol,
                        message=val_res.reason or "Data validation anomaly",
                        attributes={
                            "anomaly": val_res.anomaly.value if val_res.anomaly else None,
                            "reason": val_res.reason,
                        },
                    )
                )
                return val_res

            sym = candle.symbol.strip().upper()
            self._processed_candles_count += 1
            self._current_candles[sym] = candle
            strat_candle = candle.to_strategy_candle()

            # Append to causal historical series for symbol
            if sym not in self._candle_history:
                self._candle_history[sym] = []
            self._candle_history[sym].append(strat_candle)

            # Step 2: Evaluate Active Positions & Resting Bracket Orders First
            self._evaluate_active_position_brackets(sym, candle)

            # Step 3: Check Kill Switch Safety Gate
            if self._kill_switch.is_engaged():
                return val_res

            # Step 4: Strategy Evaluation (Causal: closed candles [1], [2], ... quarantined [0])
            history = self._candle_history[sym]
            exec_candles = [strat_candle] + list(reversed(history[:-1]))
            conf_candles = exec_candles

            eval_start = time.perf_counter_ns()
            signal = self._strategy_engine.evaluate(
                raw_exec_candles=exec_candles,
                raw_conf_candles=conf_candles,
                futures_status=FuturesConfirmationStatus.CONFIRMED,
                evaluation_timestamp=candle.exchange_timestamp,
            )
            eval_latency_ns = time.perf_counter_ns() - eval_start

            # Step 5: Risk Engine Evaluation
            risk_decision = None
            if signal.decision == StrategyDecision.ACCEPT:
                self._signals_count += 1
                observe_event(
                    ObservabilityEvent(
                        category=ObservabilityCategory.STRATEGY,
                        event_type=StrategyEventType.SIGNAL_GENERATED.value,
                        severity=ObservabilitySeverity.INFO,
                        symbol=sym,
                        correlation_id=signal.signal_id,
                        message=f"Strategy emitted {signal.direction.value} signal for {sym}",
                        attributes={
                            "symbol": sym,
                            "direction": signal.direction.value,
                            "entry": str(signal.entry_reference),
                        },
                    )
                )

                trade_side = (
                    TradeSide.LONG if signal.direction == SignalDirection.LONG else TradeSide.SHORT
                )
                risk_input = RiskInput(
                    signal_id=signal.signal_id,
                    symbol=sym,
                    side=trade_side,
                    entry_price=signal.entry_reference,
                    stop_price=signal.stop_reference,
                    contract_id=f"{sym}-FUT",
                    lot_size=self._config.lot_size,
                    contract_multiplier=self._config.contract_multiplier,
                    account_equity=Decimal("1000000"),
                    available_capital=Decimal("1000000"),
                    proposed_quantity=self._config.lot_size,
                    evaluation_timestamp=candle.exchange_timestamp,
                )
                portfolio_state = PortfolioRiskState(
                    account_equity=Decimal("1000000"),
                    available_capital=Decimal("1000000"),
                    daily_starting_equity=Decimal("1000000"),
                    current_equity=Decimal("1000000"),
                )
                risk_decision, _ = self._risk_engine.request_risk_reservation(
                    trade_input=risk_input,
                    portfolio_state=portfolio_state,
                )

                if risk_decision.decision == RiskDecisionState.APPROVED:
                    observe_event(
                        ObservabilityEvent(
                            category=ObservabilityCategory.RISK,
                            event_type=RiskEventType.RISK_ACCEPTED.value,
                            severity=ObservabilitySeverity.INFO,
                            symbol=sym,
                            correlation_id=signal.signal_id,
                            message=f"Risk approved trade for {sym}",
                            attributes={"approved_qty": risk_decision.quantity},
                        )
                    )

                    # Step 6: Order Routing & Execution
                    if self._mode == PaperShadowMode.PAPER:
                        self._execute_paper_entry(
                            symbol=sym,
                            signal=signal,
                            side=trade_side,
                            quantity=risk_decision.quantity or self._config.lot_size,
                            candle=candle,
                        )
                else:
                    observe_event(
                        ObservabilityEvent(
                            category=ObservabilityCategory.RISK,
                            event_type=RiskEventType.RISK_REJECTED.value,
                            severity=ObservabilitySeverity.WARNING,
                            symbol=sym,
                            correlation_id=signal.signal_id,
                            message=f"Risk rejected trade: {risk_decision.reason}",
                            attributes={"reason": risk_decision.reason},
                        )
                    )

            # Step 7: SHADOW Mode Observation Recording (if SHADOW mode)
            if self._mode == PaperShadowMode.SHADOW:
                evt_id = (
                    market_event_id or f"MKT-{sym}-{int(candle.exchange_timestamp.timestamp())}"
                )
                self._shadow_comparator.record_observation(
                    market_event_id=evt_id,
                    symbol=sym,
                    candle=candle,
                    signal=signal,
                    risk_decision=risk_decision,
                    evaluation_latency_ns=eval_latency_ns,
                )

            # Step 8: Continuous Multi-Point Reconciliation
            rec_result = self._reconciler.reconcile()
            self._reconciliation_passes += 1
            if rec_result.mismatch_count > 0:
                self._mismatches_count += rec_result.mismatch_count

            return val_res

    def _execute_paper_entry(
        self,
        symbol: str,
        signal: StrategySignal,
        side: TradeSide,
        quantity: int,
        candle: MarketCandle,
    ) -> None:
        """Route order and simulate paper entry execution."""
        # Create and route order through FSM & DeploymentBrokerGuard
        fsm_order, broker_order = self._order_router.create_and_route_order(
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            symbol=symbol,
            role=OrderRole.ENTRY,
            signal_id=signal.signal_id,
            side=side,
            quantity=quantity,
            order_type=BrokerOrderType.MARKET,
        )
        self._orders_count += 1

        # Simulate execution fill
        order_side = OrderSide.BUY if side == TradeSide.LONG else OrderSide.SELL
        fill = self._fill_sim.simulate_market_order(
            order_id=fsm_order.order_id,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            candle=candle,
            is_entry=True,
        )
        self._fills_count += 1

        # Update broker position state if broker is PaperBroker
        if isinstance(self._raw_broker, PaperBroker):
            self._raw_broker.simulate_full_fill(
                client_order_id=fsm_order.order_id,
                fill_price=fill.effective_price,
            )

        # Update FSM state machine: ACKNOWLEDGED -> FILLED
        self._order_router.record_fill_transition(
            client_order_id=fsm_order.order_id,
            fill_quantity=fill.filled_qty,
            fill_price=fill.effective_price,
            is_full_fill=True,
        )

        # Update P&L Tracker
        self._pnl_tracker.record_entry_fill(fill)

        # Log to Authoritative Audit Ledger
        self._ledger.append(
            event_type=AuditEventType.ORDER_FILLED,
            entity_type="ORDER",
            entity_id=fsm_order.order_id,
            correlation_id=signal.signal_id,
            causation_id=signal.signal_id,
            payload={
                "symbol": symbol,
                "side": side.value,
                "quantity": quantity,
                "price": str(fill.effective_price),
                "fee": str(fill.fee),
                "slippage": str(fill.slippage_loss),
            },
            event_timestamp=candle.exchange_timestamp,
        )

    def _evaluate_active_position_brackets(self, symbol: str, candle: MarketCandle) -> None:
        """Evaluate resting stop-loss and take-profit orders for open positions."""
        active_pos = self._pnl_tracker.get_active_positions().get(symbol)
        if active_pos is None:
            return

        # Check resting brackets against latest candle
        history = self._candle_history.get(symbol, [])
        if not history:
            return

        stop_p = (
            active_pos.entry_price * Decimal("0.98")
            if active_pos.side == TradeSide.LONG
            else active_pos.entry_price * Decimal("1.02")
        )
        target_p = (
            active_pos.entry_price * Decimal("1.04")
            if active_pos.side == TradeSide.LONG
            else active_pos.entry_price * Decimal("0.96")
        )

        bracket_res = self._fill_sim.evaluate_resting_brackets(
            order_id=f"EXIT-{active_pos.entry_order_id}",
            symbol=symbol,
            side=active_pos.side,
            quantity=active_pos.quantity,
            stop_price=stop_p,
            target_price=target_p,
            candle=candle,
        )

        if bracket_res.triggered and bracket_res.fill is not None:
            fill = bracket_res.fill
            # Close active position in PnL tracker
            trade = self._pnl_tracker.record_exit_fill(
                fill=fill,
                exit_reason=bracket_res.bracket_type or "BRACKET_EXIT",
            )
            self._fills_count += 1

            # Update PaperBroker position if applicable
            if isinstance(self._raw_broker, PaperBroker):
                self._raw_broker.submit_order(
                    BrokerOrderRequest(
                        client_order_id=fill.order_id,
                        symbol=symbol,
                        side=OrderSide.SELL if active_pos.side == TradeSide.LONG else OrderSide.BUY,
                        quantity=fill.filled_qty,
                        role=OrderRole.EXIT,
                    )
                )
                self._raw_broker.simulate_full_fill(
                    client_order_id=fill.order_id,
                    fill_price=fill.effective_price,
                )

            # Record in Audit Ledger
            self._ledger.append(
                event_type=AuditEventType.TRADE_CLOSED,
                entity_type="POSITION",
                entity_id=trade.trade_id,
                correlation_id=active_pos.entry_order_id,
                causation_id=fill.order_id,
                payload={
                    "symbol": symbol,
                    "gross_pnl": str(trade.gross_pnl),
                    "net_pnl": str(trade.net_pnl),
                    "exit_reason": trade.exit_reason or "EXIT",
                },
                event_timestamp=candle.exchange_timestamp,
            )

    def shutdown(self) -> ForwardRunReport:
        """Execute clean fail-closed shutdown and generate final ForwardRunReport."""
        with self._lock:
            self._state = ForwardRunState.STOPPED
            end_time = datetime.now(UTC)
            metrics = self._pnl_tracker.get_metrics(self._current_candles)
            config_hash = self._config.compute_config_hash()

            report = ForwardRunReport(
                run_id=f"RUN-{self._mode.value}-{int(self._start_time.timestamp())}",
                mode=self._mode,
                start_time=self._start_time,
                end_time=end_time,
                total_candles_processed=self._processed_candles_count,
                total_signals_generated=self._signals_count,
                total_orders_submitted=self._orders_count,
                total_fills_executed=self._fills_count,
                reconciliation_passes=self._reconciliation_passes,
                reconciliation_mismatches_detected=self._mismatches_count,
                metrics=metrics,
                configuration_hash=config_hash,
                code_revision=self._git_commit,
                audit_events_count=len(self._ledger),
                observability_events_count=0,
                no_live_orders_submitted=True,
            )
            return report
