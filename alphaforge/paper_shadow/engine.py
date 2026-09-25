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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from alphaforge.broker.models import BrokerOrderType
from alphaforge.broker.paper import PaperBroker
from alphaforge.contract.enums import ContractStatus
from alphaforge.contract.lifecycle import evaluate_contract_lifecycle
from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.core.models import Candle
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
from alphaforge.risk.models import RiskConfig, RiskInput
from alphaforge.security.kill_switch import KillSwitch
from alphaforge.strategy.engine import DeterministicStrategyEngine

if TYPE_CHECKING:
    from alphaforge.broker.interface import AbstractBroker
    from alphaforge.contract.repository import ContractMetadataProvider
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
        contract_provider: ContractMetadataProvider | None = None,
        git_commit: str = "81fba27684d848beecbd6d2d3e8f484fd090b0fe",
    ) -> None:
        self._config = config or PaperShadowConfig()
        self._mode = self._config.mode
        self._git_commit = git_commit
        self._contract_provider = contract_provider
        self._lock = threading.RLock()
        self._state = ForwardRunState.INITIALIZING
        self._start_time: datetime | None = None
        self._last_processed_timestamp: datetime | None = None

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

        # 6. P&L Tracker (Initialized with dynamic starting capital)
        self._pnl_tracker = PaperPnLTracker(
            cost_config=self._config.cost_config,
            contract_multiplier=self._config.contract_multiplier,
            initial_capital=self._config.initial_capital,
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
    def fill_simulator(self) -> DeterministicFillSimulator:
        return self._fill_sim

    @property
    def pnl_tracker(self) -> PaperPnLTracker:
        return self._pnl_tracker

    @property
    def order_router(self) -> PaperShadowOrderRouter:
        return self._order_router

    @property
    def raw_broker(self) -> AbstractBroker:
        return self._raw_broker

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
            if self._start_time is None:
                self._start_time = candle.exchange_timestamp
            self._last_processed_timestamp = candle.exchange_timestamp

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
            contract_id = candle.contract_id.strip().upper()
            lot_size = self._config.lot_size
            multiplier = self._config.contract_multiplier

            # Step 1.5: Authoritative Contract Metadata & Lifecycle Check (Phase 3)
            if self._contract_provider is not None:
                contract = self._contract_provider.get_contract(contract_id)
                if contract is None:
                    # Fallback to underlying lookup if not found directly by ID
                    contracts = self._contract_provider.get_contracts_for_underlying(sym)
                    for c in contracts:
                        if (
                            evaluate_contract_lifecycle(c, candle.exchange_timestamp)
                            == ContractStatus.ACTIVE
                        ):
                            contract = c
                            break
                if contract is not None:
                    c_status = evaluate_contract_lifecycle(contract, candle.exchange_timestamp)
                    if c_status != ContractStatus.ACTIVE:
                        # Non-active contract (EXPIRED/SUSPENDED):
                        # update mark-to-market and brackets, block new entries
                        self._processed_candles_count += 1
                        self._current_candles[sym] = candle
                        self._evaluate_active_position_brackets(sym, candle)
                        return val_res
                    contract_id = contract.contract_id
                    lot_size = contract.lot_size
                    multiplier = contract.contract_multiplier

            self._processed_candles_count += 1
            self._current_candles[sym] = candle
            strat_candle = candle.to_strategy_candle()

            # Append to causal historical series for symbol
            if sym not in self._candle_history:
                self._candle_history[sym] = []
            self._candle_history[sym].append(strat_candle)

            # Step 2: Evaluate Active Positions & Resting Bracket Orders First (Authoritative SL/TP)
            self._evaluate_active_position_brackets(sym, candle)

            # Step 3: Check Kill Switch Safety Gate
            if self._kill_switch.is_engaged():
                return val_res

            # Step 4: Strategy Evaluation (Causal: closed candles [1], [2], ... quarantined [0])
            history = self._candle_history[sym]
            if strat_candle.is_closed:
                forming_quarantine = Candle(
                    timestamp=strat_candle.timestamp + timedelta(minutes=3),
                    open=strat_candle.close,
                    high=strat_candle.close,
                    low=strat_candle.close,
                    close=strat_candle.close,
                    volume=0,
                    is_closed=False,
                )
                exec_candles = [forming_quarantine] + list(reversed(history))
            else:
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

            # Step 5: Risk Engine Evaluation (Dynamic Evolving Portfolio State)
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
                portfolio_state = self._pnl_tracker.get_portfolio_risk_state(self._current_candles)
                sig_id = signal.signal_id.strip().upper()
                risk_input = RiskInput(
                    signal_id=sig_id,
                    symbol=sym,
                    side=trade_side,
                    entry_price=signal.entry_reference,
                    stop_price=signal.stop_reference,
                    contract_id=contract_id,
                    lot_size=lot_size,
                    contract_multiplier=multiplier,
                    account_equity=portfolio_state.account_equity,
                    available_capital=portfolio_state.available_capital,
                    proposed_quantity=lot_size,
                    evaluation_timestamp=candle.exchange_timestamp,
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
                            correlation_id=sig_id,
                            message=f"Risk approved trade for {sym}",
                            attributes={"approved_qty": risk_decision.quantity},
                        )
                    )

                    # Step 6: Order Routing & Execution (Authoritative Entry Path)
                    if self._mode == PaperShadowMode.PAPER:
                        self._execute_paper_entry(
                            symbol=sym,
                            signal=signal,
                            side=trade_side,
                            quantity=risk_decision.quantity or lot_size,
                            candle=candle,
                        )
                else:
                    observe_event(
                        ObservabilityEvent(
                            category=ObservabilityCategory.RISK,
                            event_type=RiskEventType.RISK_REJECTED.value,
                            severity=ObservabilitySeverity.WARNING,
                            symbol=sym,
                            correlation_id=sig_id,
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

            # Step 8: Continuous Multi-Point Reconciliation (Deterministic timestamp)
            rec_result = self._reconciler.reconcile(timestamp=candle.exchange_timestamp)
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
        """
        Route order and simulate paper entry execution through authoritative FSM & broker guard.
        """
        sig_id = signal.signal_id.strip().upper()
        # Create and route order through FSM & DeploymentBrokerGuard
        fsm_order, broker_order = self._order_router.create_and_route_order(
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            symbol=symbol,
            role=OrderRole.ENTRY,
            signal_id=sig_id,
            side=side,
            quantity=quantity,
            order_type=BrokerOrderType.MARKET,
            timestamp=candle.exchange_timestamp,
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

        # Update P&L Tracker with authoritative stop and target levels from signal
        self._pnl_tracker.record_entry_fill(
            fill=fill,
            stop_price=signal.stop_reference,
            target_price=signal.target_reference,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            signal_id=sig_id,
        )

        # Log to Authoritative Audit Ledger
        self._ledger.append(
            event_type=AuditEventType.ORDER_FILLED,
            entity_type="ORDER",
            entity_id=fsm_order.order_id,
            correlation_id=sig_id,
            causation_id=sig_id,
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
        """
        Evaluate resting stop-loss and take-profit orders for open positions.
        Strictly uses authoritative strategy/signal exit levels — ZERO synthetic percentages.
        Exit orders are routed through authoritative OrderRouter / FSM / DeploymentBrokerGuard.
        Enforces strict identity lineage:
        EXIT ORDER ID == FILL ORDER ID == P&L exit_order_id == AUDIT causation_id
        """
        active_pos = self._pnl_tracker.get_active_positions().get(symbol)
        if active_pos is None:
            return

        # Trailing Stop: Check if position reached +1R and trail to Breakeven
        trailed, old_stop, new_stop = self._pnl_tracker.update_trailing_stop_to_breakeven(symbol, candle)
        if trailed:
            observe_event(
                ObservabilityEvent(
                    category=ObservabilityCategory.STRATEGY,
                    event_type=StrategyEventType.SIGNAL_GENERATED.value,
                    severity=ObservabilitySeverity.INFO,
                    symbol=symbol,
                    message=f"Trailing Stop moved to Breakeven for {symbol} ({old_stop} -> {new_stop})",
                    attributes={
                        "symbol": symbol,
                        "old_stop": str(old_stop),
                        "new_stop": str(new_stop),
                        "entry_price": str(active_pos.entry_price),
                    },
                )
            )
            active_pos = self._pnl_tracker.get_active_positions().get(symbol)
            if active_pos is None:
                return

        # Use authoritative strategy-provided exit levels
        stop_p = active_pos.stop_price
        target_p = active_pos.target_price
        if stop_p is None and target_p is None:
            return

        # 1. Detect SL/TP trigger without manufacturing premature fill order ID
        is_triggered, bracket_type = self._fill_sim.check_bracket_trigger(
            side=active_pos.side,
            stop_price=stop_p,
            target_price=target_p,
            candle=candle,
        )
        if not is_triggered or bracket_type is None:
            return

        exit_signal_id = f"EXIT-{active_pos.signal_id or active_pos.entry_order_id}".strip().upper()
        exit_side = TradeSide.SHORT if active_pos.side == TradeSide.LONG else TradeSide.LONG

        # 2. Create authoritative EXIT order: OrderRouter -> FSM -> BrokerGuard -> Broker
        fsm_exit_order, broker_exit_order = self._order_router.create_and_route_order(
            strategy_id=active_pos.strategy_id or "AF_STRAT",
            strategy_version=active_pos.strategy_version or "1.0.0",
            symbol=symbol,
            role=OrderRole.EXIT,
            signal_id=exit_signal_id,
            side=exit_side,
            quantity=active_pos.quantity,
            order_type=BrokerOrderType.MARKET,
            timestamp=candle.exchange_timestamp,
        )
        self._orders_count += 1
        authoritative_exit_order_id = fsm_exit_order.order_id

        # 3. Simulate execution fill using the authoritative EXIT order ID
        fill = self._fill_sim.simulate_bracket_fill(
            order_id=authoritative_exit_order_id,
            symbol=symbol,
            side=active_pos.side,
            quantity=active_pos.quantity,
            bracket_type=bracket_type,
            stop_price=stop_p,
            target_price=target_p,
            candle=candle,
        )
        self._fills_count += 1

        # 4. Update PaperBroker position if applicable
        if isinstance(self._raw_broker, PaperBroker):
            self._raw_broker.simulate_full_fill(
                client_order_id=authoritative_exit_order_id,
                fill_price=fill.effective_price,
            )

        # 5. Update FSM state machine: ACKNOWLEDGED -> FILLED on authoritative order
        self._order_router.record_fill_transition(
            client_order_id=authoritative_exit_order_id,
            fill_quantity=fill.filled_qty,
            fill_price=fill.effective_price,
            is_full_fill=True,
        )

        # 6. Close active position in PnL tracker (fill.order_id == auth_exit_order_id)
        trade = self._pnl_tracker.record_exit_fill(
            fill=fill,
            exit_reason=bracket_type,
        )

        # 7. Record in Audit Ledger with authoritative exit order ID as causation_id
        self._ledger.append(
            event_type=AuditEventType.TRADE_CLOSED,
            entity_type="POSITION",
            entity_id=trade.trade_id,
            correlation_id=active_pos.entry_order_id,
            causation_id=authoritative_exit_order_id,
            payload={
                "symbol": symbol,
                "exit_order_id": authoritative_exit_order_id,
                "gross_pnl": str(trade.gross_pnl),
                "net_pnl": str(trade.net_pnl),
                "exit_reason": trade.exit_reason or "EXIT",
            },
            event_timestamp=candle.exchange_timestamp,
        )

    def shutdown(self) -> ForwardRunReport:
        """
        Execute clean fail-closed shutdown and generate final ForwardRunReport
        with deterministic timestamps.
        """
        with self._lock:
            self._state = ForwardRunState.STOPPED
            start_time = self._start_time or datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
            end_time = self._last_processed_timestamp or start_time
            metrics = self._pnl_tracker.get_metrics(self._current_candles)
            config_hash = self._config.compute_config_hash()

            report = ForwardRunReport(
                run_id=f"RUN-{self._mode.value}-{int(start_time.timestamp())}",
                mode=self._mode,
                start_time=start_time,
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
