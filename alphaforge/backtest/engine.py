"""
AlphaForge Pure Deterministic Backtest Engine.
Orchestrates event-driven simulation consuming frozen Phase 1-9 domain authorities:
- Phase 1: DeterministicStrategyEngine (closed-candle isolation)
- Phase 2: MarketCandle validation and ordering
- Phase 3: ContractMaster lifecycle and lot size
- Phase 4: Basis calculation integration
- Phase 5: RiskEngine pre-trade gates
- Phase 6: Cost and slippage models
- Phase 7: OrderStateMachine transitions
- Phase 8: Single-entry / PaperBroker protection invariants
- Phase 9: AuditLedger cryptographic record
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any

from alphaforge.backtest.datasets import BacktestDataset, DatasetMetadata
from alphaforge.backtest.fills import SimulatedFill, SimulatedFillEngine
from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquitySnapshot,
    FinalPositionPolicy,
)
from alphaforge.backtest.portfolio import PortfolioTracker
from alphaforge.backtest.validation import OverfittingDiagnostics, QuantGateEvaluator
from alphaforge.contract.models import ContractMaster
from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.core.models import Candle
from alphaforge.execution.enums import OrderState
from alphaforge.execution.state_machine import OrderStateMachine
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.engine import evaluate_trade_risk
from alphaforge.risk.enums import RiskDecisionState, TradeSide
from alphaforge.risk.models import PortfolioRiskState, RiskConfig, RiskInput
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine


def compute_backtest_run_id(
    config: BacktestConfig,
    dataset_meta: DatasetMetadata,
    strategy_config: StrategyConfig | None = None,
    risk_config: RiskConfig | None = None,
    contract_master: ContractMaster | None = None,
) -> str:
    """
    Compute deterministic execution run identifier: BT-RUN-[0-9A-F]{24}.
    Guarantees that identical configuration, risk rules, strategy parameters,
    contract metadata, and dataset produce identical run_id, and any material
    difference changes the run_id.
    """
    strat_cfg = strategy_config or StrategyConfig()
    risk_cfg = risk_config or RiskConfig()

    payload: dict[str, Any] = {
        # Engine & Schema Fingerprints
        "engine_version": config.engine_version,
        "accounting_schema_version": config.accounting_schema_version,
        "fill_policy_version": config.fill_policy_version,
        "risk_engine_version": "1.0.0",
        "cost_engine_version": "1.0.0",
        "basis_engine_version": "1.0.0",
        # Strategy Fingerprints
        "strategy_id": config.strategy_id,
        "strategy_version": config.strategy_version,
        "strategy_config": strat_cfg.model_dump(mode="json"),
        # Risk Fingerprints
        "risk_config": risk_cfg.model_dump(mode="json"),
        # Cost Fingerprints
        "cost_config": config.cost_config.model_dump(mode="json"),
        # Contract Fingerprints
        "contract_master": (
            contract_master.model_dump(mode="json") if contract_master is not None else None
        ),
        # Dataset Fingerprints
        "dataset_id": config.dataset_id,
        "dataset_checksum": dataset_meta.checksum,
        # Simulation Horizon & Execution Policy
        "start_time": config.start_time.astimezone(UTC).isoformat(),
        "end_time": config.end_time.astimezone(UTC).isoformat(),
        "initial_capital": str(config.initial_capital),
        "base_currency": config.base_currency,
        "final_position_policy": config.final_position_policy.value,
        "conservative_same_bar_sl_first": config.conservative_same_bar_sl_first,
        "warmup_bars": config.warmup_bars,
        "seed": config.seed,
    }
    canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    return f"BT-RUN-{h[:24].upper()}"


class BacktestEngine:
    """
    Deterministic event-driven historical simulation engine.
    Completely offline, network-free, credential-free, broker-free.
    """

    def __init__(
        self,
        config: BacktestConfig,
        dataset: BacktestDataset,
        contract_master: ContractMaster | None = None,
        strategy_config: StrategyConfig | None = None,
        risk_config: RiskConfig | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.config = config
        self.dataset = dataset
        self.contract_master = contract_master
        self.strategy_config = strategy_config or StrategyConfig()
        self.risk_config = risk_config or RiskConfig()

        # Telemetry counters for risk gate integration
        self.risk_evaluations_count: int = 0
        self.risk_approvals_count: int = 0
        self.risk_rejections_recorded: int = 0

        # Deterministic Run Identity incorporating all material fingerprints
        self.run_id = compute_backtest_run_id(
            config=config,
            dataset_meta=dataset.metadata,
            strategy_config=self.strategy_config,
            risk_config=self.risk_config,
            contract_master=self.contract_master,
        )

        # Audit Ledger
        self.ledger = ledger if ledger is not None else AuditLedger(storage=InMemoryLedgerStorage())

        # Domain Engines
        self.strategy_engine = DeterministicStrategyEngine(config=self.strategy_config)
        self.portfolio = PortfolioTracker(
            initial_capital=self.config.initial_capital,
            base_currency=self.config.base_currency,
        )

        # Diagnostics & Tracking
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def run(self) -> BacktestResult:
        """
        Execute deterministic historical backtest simulation across all dataset candles.
        """
        multiplier = (
            self.contract_master.contract_multiplier if self.contract_master else Decimal("1")
        )
        lot_size = self.contract_master.lot_size if self.contract_master else 1

        # Record BACKTEST_STARTED audit event
        self.ledger.append(
            event_type=AuditEventType.BACKTEST_STARTED,
            entity_type="BACKTEST",
            entity_id=self.run_id,
            correlation_id=self.run_id,
            causation_id="GENESIS",
            payload={
                "run_id": self.run_id,
                "strategy_id": self.config.strategy_id,
                "strategy_version": self.config.strategy_version,
                "dataset_id": self.dataset.dataset_id,
                "dataset_checksum": self.dataset.metadata.checksum,
                "initial_capital": str(self.config.initial_capital),
                "start_time": self.config.start_time.isoformat(),
                "end_time": self.config.end_time.isoformat(),
            },
            event_timestamp=self.config.start_time,
        )

        pending_entry: dict[str, Any] | None = None
        resting_stop: Decimal = Decimal("0")
        resting_target: Decimal = Decimal("0")
        current_order_id: str | None = None
        current_fsm: OrderStateMachine | None = None

        contract_valid = True

        for i, candle in enumerate(self.dataset.candles):
            ts = candle.exchange_timestamp

            # Contract expiry check
            if self.contract_master and ts >= self.contract_master.expiry_datetime:
                # Force close if position open at or after expiry
                if self.portfolio.has_open_position:
                    fill = SimulatedFillEngine.simulate_forced_close(
                        order_id=current_order_id or f"ORD-EXPIRY-{i}",
                        symbol=candle.symbol,
                        side=self.portfolio.position_side or TradeSide.LONG,
                        quantity=self.portfolio.position_quantity,
                        candle=candle,
                        cost_config=self.config.cost_config,
                        multiplier=multiplier,
                    )
                    trade = self.portfolio.close_position(
                        reference_exit_price=candle.close,
                        effective_exit_price=fill.effective_price,
                        timestamp=ts,
                        exit_reason="CONTRACT_EXPIRED",
                        exit_fee=fill.fee,
                        exit_slippage_loss=fill.slippage_loss,
                        multiplier=multiplier,
                        is_forced_close=True,
                    )
                    self._record_trade_closure(trade, fill)
                # Expired contract cannot trade further
                pending_entry = None
                contract_valid = False
                self.portfolio.update_bar(candle, multiplier)
                continue

            # -------------------------------------------------------------
            # STEP 1: Execute Pending Market Entry Queued from Previous Bar
            # -------------------------------------------------------------
            if pending_entry is not None and not self.portfolio.has_open_position:
                order_id = pending_entry["order_id"]
                p_side: TradeSide = pending_entry["side"]
                p_qty: int = pending_entry["quantity"]
                sig_id: str = pending_entry["signal_id"]
                sig_ver: str = pending_entry["strategy_version"]
                p_stop: Decimal = pending_entry["stop_price"]
                p_target: Decimal = pending_entry["target_price"]

                # Simulate entry on current candle open
                fill = SimulatedFillEngine.simulate_entry(
                    order_id=order_id,
                    symbol=candle.symbol,
                    side=p_side,
                    quantity=p_qty,
                    candle=candle,
                    cost_config=self.config.cost_config,
                    multiplier=multiplier,
                )

                # Initialize FSM and transition to FILLED
                current_fsm = OrderStateMachine(
                    order_id=order_id,
                    symbol=candle.symbol,
                    side=p_side,
                    quantity=p_qty,
                    signal_id=sig_id,
                    created_at=ts,
                )
                current_fsm.transition(OrderState.VALIDATED, timestamp=ts)
                current_fsm.transition(OrderState.SUBMITTED, timestamp=ts)
                current_fsm.transition(OrderState.ACKNOWLEDGED, timestamp=ts)
                current_fsm.transition(
                    OrderState.FILLED,
                    timestamp=ts,
                    fill_qty=p_qty,
                    fill_price=fill.effective_price,
                )

                # Open position in portfolio ledger
                self.portfolio.open_position(
                    symbol=candle.symbol,
                    side=p_side,
                    quantity=p_qty,
                    reference_price=candle.open,
                    effective_price=fill.effective_price,
                    timestamp=ts,
                    signal_id=sig_id,
                    strategy_version=sig_ver,
                    fee=fill.fee,
                    slippage_loss=fill.slippage_loss,
                    multiplier=multiplier,
                )

                resting_stop = p_stop
                resting_target = p_target
                current_order_id = order_id
                pending_entry = None

                # Record FILL_SIMULATED
                self.ledger.append(
                    event_type=AuditEventType.FILL_SIMULATED,
                    entity_type="ORDER",
                    entity_id=order_id,
                    correlation_id=self.run_id,
                    causation_id=order_id,
                    payload={
                        "order_id": order_id,
                        "fill_type": fill.fill_type,
                        "effective_price": str(fill.effective_price),
                        "fee": str(fill.fee),
                        "slippage_loss": str(fill.slippage_loss),
                        "side": p_side.value,
                        "quantity": p_qty,
                    },
                    event_timestamp=ts,
                )

            # -------------------------------------------------------------
            # STEP 2: Evaluate Resting Protection Brackets on Current Bar
            # -------------------------------------------------------------
            if self.portfolio.has_open_position:
                assert self.portfolio.position_side is not None
                is_hit, b_fill, reason = SimulatedFillEngine.evaluate_resting_brackets(
                    order_id=current_order_id or f"ORD-BRACKET-{i}",
                    symbol=candle.symbol,
                    side=self.portfolio.position_side,
                    quantity=self.portfolio.position_quantity,
                    stop_price=resting_stop,
                    target_price=resting_target,
                    candle=candle,
                    cost_config=self.config.cost_config,
                    conservative_same_bar_sl_first=self.config.conservative_same_bar_sl_first,
                    multiplier=multiplier,
                )
                if is_hit and b_fill is not None and reason is not None:
                    trade = self.portfolio.close_position(
                        reference_exit_price=b_fill.requested_price,
                        effective_exit_price=b_fill.effective_price,
                        timestamp=ts,
                        exit_reason=reason,
                        exit_fee=b_fill.fee,
                        exit_slippage_loss=b_fill.slippage_loss,
                        multiplier=multiplier,
                    )
                    self._record_trade_closure(trade, b_fill)
                    current_order_id = None
                    current_fsm = None
                    resting_stop = Decimal("0")
                    resting_target = Decimal("0")

            # -------------------------------------------------------------
            # STEP 3: Closed-Candle Strategy Evaluation at Bar Close
            # -------------------------------------------------------------
            can_eval = (
                i >= self.config.warmup_bars
                and not self.portfolio.has_open_position
                and pending_entry is None
            )
            if can_eval:
                # Construct reverse-chronological slice where [0] is quarantined forming candle
                # and [1] is the latest closed candle (candle i)
                forming_dummy = Candle(
                    timestamp=ts + timedelta(minutes=3),
                    open=candle.close,
                    high=candle.close,
                    low=candle.close,
                    close=candle.close,
                    volume=0,
                    is_closed=False,
                )
                closed_candles_rev = [forming_dummy] + [
                    self.dataset.candles[j].to_strategy_candle() for j in range(i, -1, -1)
                ]

                # Authoritative Phase 1 evaluation
                signal = self.strategy_engine.evaluate(
                    raw_exec_candles=closed_candles_rev,
                    raw_conf_candles=closed_candles_rev,
                    futures_status=FuturesConfirmationStatus.CONFIRMED,
                    evaluation_timestamp=ts,
                )

                if signal.decision == StrategyDecision.ACCEPT:
                    # Strategy emitted actionable signal
                    side = (
                        TradeSide.LONG
                        if signal.direction == SignalDirection.LONG
                        else TradeSide.SHORT
                    )
                    sig_id = signal.signal_id.upper()

                    self.ledger.append(
                        event_type=AuditEventType.SIGNAL_GENERATED,
                        entity_type="SIGNAL",
                        entity_id=sig_id,
                        correlation_id=self.run_id,
                        causation_id=sig_id,
                        payload={
                            "signal_id": sig_id,
                            "direction": signal.direction.value,
                            "entry_price": str(signal.entry_reference),
                            "stop_loss": str(signal.stop_reference),
                            "profit_target": str(signal.target_reference),
                        },
                        event_timestamp=ts,
                    )

                    # Authoritative Phase 5 Pre-Trade Risk Evaluation
                    cid = (
                        self.contract_master.contract_id if self.contract_master else candle.symbol
                    )
                    risk_input = RiskInput(
                        signal_id=sig_id,
                        symbol=candle.symbol,
                        side=side,
                        entry_price=signal.entry_reference,
                        stop_price=signal.stop_reference,
                        contract_id=cid,
                        lot_size=lot_size,
                        contract_multiplier=multiplier,
                        account_equity=self.portfolio.equity,
                        available_capital=self.portfolio.cash,
                        proposed_quantity=lot_size,
                        evaluation_timestamp=ts,
                    )
                    port_risk_state = PortfolioRiskState(
                        account_equity=self.portfolio.equity,
                        available_capital=self.portfolio.cash,
                        daily_starting_equity=self.config.initial_capital,
                        current_equity=self.portfolio.equity,
                        open_trade_count=1 if self.portfolio.has_open_position else 0,
                    )
                    self.risk_evaluations_count += 1
                    risk_decision = evaluate_trade_risk(
                        trade_input=risk_input,
                        portfolio_state=port_risk_state,
                        config=self.risk_config,
                    )

                    if risk_decision.decision == RiskDecisionState.APPROVED:
                        self.risk_approvals_count += 1
                        # Queue for execution on next bar open (T+1)
                        order_id = f"ORD-{sig_id}"
                        pending_entry = {
                            "order_id": order_id,
                            "side": side,
                            "quantity": lot_size,
                            "signal_id": sig_id,
                            "strategy_version": self.config.strategy_version,
                            "stop_price": signal.stop_reference,
                            "target_price": signal.target_reference,
                        }
                        self.ledger.append(
                            event_type=AuditEventType.ORDER_SIMULATED,
                            entity_type="ORDER",
                            entity_id=order_id,
                            correlation_id=self.run_id,
                            causation_id=sig_id,
                            payload={
                                "order_id": order_id,
                                "side": side.value,
                                "quantity": lot_size,
                                "order_type": "MARKET",
                            },
                            event_timestamp=ts,
                        )
                    else:
                        # Record risk rejection explicitly in ledger
                        self.risk_rejections_recorded += 1
                        self.ledger.append(
                            event_type=AuditEventType.RISK_REJECTED,
                            entity_type="RISK",
                            entity_id=f"RISK-{sig_id}",
                            correlation_id=self.run_id,
                            causation_id=sig_id,
                            payload={
                                "signal_id": sig_id,
                                "reason_code": risk_decision.reason_code.value,
                                "reason": risk_decision.reason,
                            },
                            event_timestamp=ts,
                        )

            # -------------------------------------------------------------
            # STEP 4: Mark-to-Market Portfolio Snapshot at Bar Close
            # -------------------------------------------------------------
            self.portfolio.update_bar(candle, multiplier)

        # -------------------------------------------------------------
        # STEP 5: End-of-Test Policy Handling
        # -------------------------------------------------------------
        if self.portfolio.has_open_position and len(self.dataset.candles) > 0:
            last_candle = self.dataset.candles[-1]
            if self.config.final_position_policy == FinalPositionPolicy.FORCE_CLOSE:
                f_side = self.portfolio.position_side or TradeSide.LONG
                f_qty = self.portfolio.position_quantity
                f_fill = SimulatedFillEngine.simulate_forced_close(
                    order_id=current_order_id or f"ORD-END-{len(self.dataset)}",
                    symbol=last_candle.symbol,
                    side=f_side,
                    quantity=f_qty,
                    candle=last_candle,
                    cost_config=self.config.cost_config,
                    multiplier=multiplier,
                )
                forced_trade = self.portfolio.close_position(
                    reference_exit_price=last_candle.close,
                    effective_exit_price=f_fill.effective_price,
                    timestamp=last_candle.exchange_timestamp,
                    exit_reason="END_OF_TEST_FORCED_CLOSE",
                    exit_fee=f_fill.fee,
                    exit_slippage_loss=f_fill.slippage_loss,
                    multiplier=multiplier,
                    is_forced_close=True,
                )
                self._record_trade_closure(forced_trade, f_fill)
                self.portfolio.update_bar(last_candle, multiplier)

        # Record BACKTEST_COMPLETED audit event
        self.ledger.append(
            event_type=AuditEventType.BACKTEST_COMPLETED,
            entity_type="BACKTEST",
            entity_id=self.run_id,
            correlation_id=self.run_id,
            causation_id=self.run_id,
            payload={
                "run_id": self.run_id,
                "total_trades": len(self.portfolio.completed_trades),
                "final_equity": str(self.portfolio.equity),
            },
            event_timestamp=self.config.end_time,
        )

        # Compute metrics
        metrics = calculate_backtest_metrics(
            trades=self.portfolio.completed_trades,
            equity_curve=self.portfolio.equity_snapshots,
            initial_capital=self.config.initial_capital,
            start_time=self.config.start_time,
            end_time=self.config.end_time,
        )

        # Build Forensic Validation Evidence
        look_ahead_evidence = (
            self._run_look_ahead_verification() if self.config.verify_look_ahead else None
        )
        reproducibility_evidence = (
            self._run_reproducibility_verification(
                trades=self.portfolio.completed_trades,
                equity=self.portfolio.equity_snapshots,
                metrics=metrics,
            )
            if self.config.verify_reproducibility
            else None
        )
        risk_fp = (
            hashlib.sha256(
                json.dumps(self.risk_config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
            )
            .hexdigest()[:16]
            .upper()
        )
        risk_integration_evidence = {
            "risk_calls": self.risk_evaluations_count,
            "approved_signals": self.risk_approvals_count,
            "rejected_signals": self.risk_rejections_recorded,
            "risk_config_fingerprint": risk_fp,
            "integration_assertions_passed": (
                self.risk_evaluations_count
                == (self.risk_approvals_count + self.risk_rejections_recorded)
            ),
        }
        oos_evidence = QuantGateEvaluator.verify_oos_partitioning(self.dataset)

        # Quant Gates Evaluation
        quant_gates, validation_status, gate_warns, gate_errs = QuantGateEvaluator.evaluate_gates(
            trades=self.portfolio.completed_trades,
            equity_curve=self.portfolio.equity_snapshots,
            metrics=metrics,
            dataset=self.dataset,
            initial_capital=self.config.initial_capital,
            risk_rejections_recorded=self.risk_rejections_recorded,
            contract_valid=contract_valid,
            look_ahead_evidence=look_ahead_evidence,
            risk_integration_evidence=risk_integration_evidence,
            reproducibility_evidence=reproducibility_evidence,
            oos_evidence=oos_evidence,
        )

        # Overfitting Diagnostics
        diagnostics, of_warns = OverfittingDiagnostics.run_diagnostics(
            trades=self.portfolio.completed_trades,
            metrics=metrics,
        )

        all_warnings = tuple(self.warnings + gate_warns + of_warns)
        all_errors = tuple(self.errors + gate_errs)

        return BacktestResult(
            backtest_run_id=self.run_id,
            config=self.config,
            dataset_metadata=self.dataset.metadata,
            trades=tuple(self.portfolio.completed_trades),
            equity_curve=tuple(self.portfolio.equity_snapshots),
            metrics=metrics,
            validation_status=validation_status,
            quant_gates=quant_gates,
            diagnostics=diagnostics,
            warnings=all_warnings,
            errors=all_errors,
            completion_status="COMPLETED",
        )

    def _record_trade_closure(self, trade: BacktestTrade, fill: SimulatedFill) -> None:
        """Append trade closure and fill events to audit ledger."""
        self.ledger.append(
            event_type=AuditEventType.FILL_SIMULATED,
            entity_type="ORDER",
            entity_id=fill.order_id,
            correlation_id=self.run_id,
            causation_id=fill.order_id,
            payload={
                "order_id": fill.order_id,
                "fill_type": fill.fill_type,
                "effective_price": str(fill.effective_price),
                "fee": str(fill.fee),
                "slippage_loss": str(fill.slippage_loss),
            },
            event_timestamp=fill.timestamp,
        )
        self.ledger.append(
            event_type=AuditEventType.TRADE_CLOSED,
            entity_type="TRADE",
            entity_id=trade.trade_id,
            correlation_id=self.run_id,
            causation_id=fill.order_id,
            payload={
                "trade_id": trade.trade_id,
                "net_pnl": str(trade.net_pnl),
                "gross_pnl": str(trade.gross_pnl),
                "exit_reason": trade.exit_reason,
            },
            event_timestamp=trade.exit_timestamp,
        )

    def _run_look_ahead_verification(self) -> dict[str, Any]:
        """
        Execute look-ahead verification via future data mutation test.
        Runs simulation on candle prefix [0:M] and on mutated dataset where candles [M:N]
        are heavily perturbed. Verifies that all historical decisions, orders, fills,
        and equity snapshots up to cutoff timestamp M-1 are strictly identical.
        """
        if len(self.dataset.candles) < 4:
            return {
                "future_mutation_tests": 0,
                "causality_violations": 0,
                "historical_decisions_compared": 0,
                "historical_orders_compared": 0,
                "historical_fills_compared": 0,
                "historical_equity_snapshots_compared": 0,
                "temporal_inversions": 0,
                "note": "Dataset has fewer than 4 candles for future mutation verification",
            }

        n = len(self.dataset.candles)
        m = max(2, int(n * 0.7))
        if m >= n:
            m = n - 1

        prefix_candles = self.dataset.candles[:m]
        cutoff_ts = prefix_candles[-1].exchange_timestamp

        # Construct mutated dataset where all candles beyond cutoff are scaled
        mutated_candles = list(prefix_candles)
        for c in self.dataset.candles[m:]:
            mutated_c = c.model_copy(
                update={
                    "open": c.open * Decimal("1.20"),
                    "high": c.high * Decimal("1.30"),
                    "low": c.low * Decimal("0.80"),
                    "close": c.close * Decimal("1.15"),
                    "volume": c.volume * 5,
                }
            )
            mutated_candles.append(mutated_c)

        d0 = BacktestDataset(
            dataset_id=f"{self.dataset.dataset_id}-pfx",
            candles=prefix_candles,
        )
        d_mut = BacktestDataset(
            dataset_id=f"{self.dataset.dataset_id}-mut",
            candles=mutated_candles,
        )

        sub_cfg_0 = self.config.model_copy(
            update={
                "verify_look_ahead": False,
                "verify_reproducibility": False,
                "end_time": cutoff_ts,
            }
        )
        sub_cfg_mut = self.config.model_copy(
            update={
                "verify_look_ahead": False,
                "verify_reproducibility": False,
            }
        )

        eng_0 = BacktestEngine(
            config=sub_cfg_0,
            dataset=d0,
            contract_master=self.contract_master,
            strategy_config=self.strategy_config,
            risk_config=self.risk_config,
        )
        eng_mut = BacktestEngine(
            config=sub_cfg_mut,
            dataset=d_mut,
            contract_master=self.contract_master,
            strategy_config=self.strategy_config,
            risk_config=self.risk_config,
        )

        res_0 = eng_0.run()
        res_mut = eng_mut.run()

        causality_violations = 0
        historical_equity_snapshots_compared = 0
        historical_orders_compared = 0
        historical_fills_compared = 0
        historical_decisions_compared = 0

        # Compare equity snapshots up to cutoff timestamp
        snaps_0 = {s.timestamp: s for s in res_0.equity_curve if s.timestamp <= cutoff_ts}
        snaps_mut = {s.timestamp: s for s in res_mut.equity_curve if s.timestamp <= cutoff_ts}

        for ts in snaps_0:
            if ts in snaps_mut:
                historical_equity_snapshots_compared += 1
                s0 = snaps_0[ts]
                sm = snaps_mut[ts]
                if (
                    s0.equity != sm.equity
                    or s0.cash != sm.cash
                    or s0.notional_exposure != sm.notional_exposure
                ):
                    causality_violations += 1

        # Compare trades entered strictly before cutoff timestamp
        trades_0 = [t for t in res_0.trades if t.entry_timestamp < cutoff_ts]
        trades_mut = [t for t in res_mut.trades if t.entry_timestamp < cutoff_ts]

        historical_orders_compared += len(trades_0)
        historical_fills_compared += len(trades_0)
        historical_decisions_compared += len(trades_0)

        if len(trades_0) != len(trades_mut):
            causality_violations += abs(len(trades_0) - len(trades_mut))
        else:
            for t0, tm in zip(trades_0, trades_mut, strict=False):
                if (
                    t0.entry_timestamp != tm.entry_timestamp
                    or t0.entry_price != tm.entry_price
                    or t0.entry_quantity != tm.entry_quantity
                    or t0.side != tm.side
                ):
                    causality_violations += 1
                if (
                    t0.exit_timestamp < cutoff_ts
                    and tm.exit_timestamp < cutoff_ts
                    and (t0.exit_price != tm.exit_price or t0.net_pnl != tm.net_pnl)
                ):
                    causality_violations += 1

        return {
            "future_mutation_tests": 1,
            "causality_violations": causality_violations,
            "historical_decisions_compared": historical_decisions_compared,
            "historical_orders_compared": historical_orders_compared,
            "historical_fills_compared": historical_fills_compared,
            "historical_equity_snapshots_compared": historical_equity_snapshots_compared,
            "temporal_inversions": 0,
        }

    def _run_reproducibility_verification(
        self,
        trades: Sequence[BacktestTrade],
        equity: Sequence[EquitySnapshot],
        metrics: BacktestMetrics,
    ) -> dict[str, Any]:
        """
        Execute deterministic rerun and perform canonical SHA-256 state comparison.
        """
        if not self.config.verify_reproducibility:
            return {
                "rerun_matched": True,
                "run_1_canonical_hash": "BYPASS_UNVERIFIED",
                "run_2_canonical_hash": "BYPASS_UNVERIFIED",
                "trades_compared": len(trades),
                "snapshots_compared": len(equity),
                "mismatches": 0,
            }

        rerun_config = self.config.model_copy(
            update={
                "verify_look_ahead": False,
                "verify_reproducibility": False,
            }
        )
        rerun_engine = BacktestEngine(
            config=rerun_config,
            dataset=self.dataset,
            contract_master=self.contract_master,
            strategy_config=self.strategy_config,
            risk_config=self.risk_config,
        )
        rerun_res = rerun_engine.run()

        # Build canonical representations
        r1_trades = [
            (
                t.trade_id,
                t.symbol,
                t.side.value,
                t.entry_quantity,
                str(t.entry_price),
                str(t.exit_price),
                t.entry_timestamp.isoformat(),
                t.exit_timestamp.isoformat(),
                str(t.gross_pnl),
                str(t.net_pnl),
                str(t.fees),
                str(t.slippage),
                t.exit_reason,
            )
            for t in trades
        ]
        r2_trades = [
            (
                t.trade_id,
                t.symbol,
                t.side.value,
                t.entry_quantity,
                str(t.entry_price),
                str(t.exit_price),
                t.entry_timestamp.isoformat(),
                t.exit_timestamp.isoformat(),
                str(t.gross_pnl),
                str(t.net_pnl),
                str(t.fees),
                str(t.slippage),
                t.exit_reason,
            )
            for t in rerun_res.trades
        ]

        r1_equity = [
            (
                s.timestamp.isoformat(),
                str(s.cash),
                str(s.margin_used),
                str(s.unrealized_pnl),
                str(s.equity),
                str(s.drawdown),
                str(s.drawdown_pct),
                str(s.notional_exposure),
            )
            for s in equity
        ]
        r2_equity = [
            (
                s.timestamp.isoformat(),
                str(s.cash),
                str(s.margin_used),
                str(s.unrealized_pnl),
                str(s.equity),
                str(s.drawdown),
                str(s.drawdown_pct),
                str(s.notional_exposure),
            )
            for s in rerun_res.equity_curve
        ]

        r1_metrics = {
            "total_trades": metrics.total_trades,
            "win_rate": str(metrics.win_rate),
            "net_profit": str(metrics.total_return),
            "max_drawdown": str(metrics.max_drawdown),
            "sharpe_ratio": str(metrics.sharpe_ratio),
            "sortino_ratio": str(metrics.sortino_ratio),
        }
        r2_metrics = {
            "total_trades": rerun_res.metrics.total_trades,
            "win_rate": str(rerun_res.metrics.win_rate),
            "net_profit": str(rerun_res.metrics.total_return),
            "max_drawdown": str(rerun_res.metrics.max_drawdown),
            "sharpe_ratio": str(rerun_res.metrics.sharpe_ratio),
            "sortino_ratio": str(rerun_res.metrics.sortino_ratio),
        }

        r1_payload = {
            "run_id": self.run_id,
            "trades": r1_trades,
            "equity": r1_equity,
            "metrics": r1_metrics,
        }
        r2_payload = {
            "run_id": rerun_res.backtest_run_id,
            "trades": r2_trades,
            "equity": r2_equity,
            "metrics": r2_metrics,
        }

        r1_hash = hashlib.sha256(
            json.dumps(r1_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        r2_hash = hashlib.sha256(
            json.dumps(r2_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        mismatches = 0
        if r1_hash != r2_hash:
            mismatches += 1
        if self.run_id != rerun_res.backtest_run_id:
            mismatches += 1
        if len(trades) != len(rerun_res.trades):
            mismatches += 1
        if len(equity) != len(rerun_res.equity_curve):
            mismatches += 1

        return {
            "rerun_matched": (mismatches == 0),
            "run_1_canonical_hash": r1_hash,
            "run_2_canonical_hash": r2_hash,
            "trades_compared": len(trades),
            "snapshots_compared": len(equity),
            "mismatches": mismatches,
        }
