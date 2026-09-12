# ALPHAFORGE — FORMAL REQUIREMENT TRACEABILITY MATRIX

**Project Name:** AlphaForge  
**Document Type:** Formal System Requirements Specification (SRS)  
**Phase:** Phase 0 — Requirement Freeze  
**Author:** Principal Software Architect, Senior Quantitative Trading Systems Engineer, Reliability Engineer, Security Engineer, and Code Auditor  
**Date:** 2026-09-12  
**Status:** APPROVED REQUIREMENTS CONTRACT  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan (Master Specification)

---

## 1. Requirement Traceability Schema

Every requirement is codified according to the strict institutional schema:
- **Requirement ID:** Unique stable identifier (`AF-REQ-xxx`).
- **Description:** Clear, unambiguous statement of system behavior.
- **Source:** Direct clause from the Master Specification.
- **Input:** Specific triggers, data structures, or preconditions.
- **Output:** Concrete state transitions, data outputs, or events emitted.
- **Owner:** Architectural subsystem responsible for enforcement.
- **Priority:** P0 (Catastrophic/Safety), P1 (Critical), P2 (High), P3 (Medium).
- **Acceptance Criteria:** Verifiable conditions required for sign-off.
- **Failure Behavior:** Deterministic system response upon invalid input or error.
- **Test Reference:** Test tier, methodology, or fixture verifying the requirement.
- **Status:** Frozen (Phase 0).

---

## 2. Master Specification Requirements Matrix

### 2.1 Strategy & Feature Generation Subsystem

#### AF-REQ-001: Deterministic Strategy Execution
- **Description:** Given identical market data inputs, identical versioned configuration parameters, and identical dependencies, the strategy must produce bit-exact identical trading signals.
- **Source:** Master Specification Section 3 ("Determinism") & Section 5 ("V1 Scope").
- **Input:** Validated sequence of closed historical candles + immutable strategy configuration.
- **Output:** Deterministic `SignalIntent` or explicit `SignalRejection`.
- **Owner:** Strategy Engine.
- **Priority:** P1.
- **Acceptance Criteria:** 10,000 independent runs on static historical fixtures yield identical signals and timestamps.
- **Failure Behavior:** If non-deterministic behavior or floating-point drift is detected, engine halts with `ERR_STRATEGY_NON_DETERMINISTIC`.
- **Test Reference:** `tests/unit/strategy/test_determinism.py`, `tests/golden/test_golden_signals.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-002: Closed-Candle Rule Enforcement ([1] vs [0])
- **Description:** Signal generation and indicator calculations must operate strictly on candle `[1]` (latest fully closed candle) and older historical bars. Candle `[0]` (forming candle) is strictly quarantined.
- **Source:** Master Specification Section 8 ("Closed-Candle Rule").
- **Input:** Raw ingested candle sequence where `[0]` is current forming and `[1]` is closed.
- **Output:** Sliced candle window containing only `[1..N]` passed to strategy evaluation.
- **Owner:** Data Ingestion & Strategy Engine Boundary.
- **Priority:** P0.
- **Acceptance Criteria:** Property-based tests prove that mutating candle `[0]` high/low/close/volume produces zero difference in emitted signals.
- **Failure Behavior:** Any attempt by strategy code to access `[0]` raises `ClosedCandleViolationError` and emits `REJECT_CLOSED_CANDLE_VIOLATION`.
- **Test Reference:** `tests/property/test_closed_candle_invariant.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-003: Multi-Timeframe Signal Synchronization
- **Description:** Strategy must generate signals using aligned multi-timeframe inputs: primary execution timeframe ($TF_{exec}$) and higher confirmation timeframe ($TF_{conf}$).
- **Source:** Master Specification Section 5 ("V1 Scope").
- **Input:** Parallel synchronized streams of closed candles on $TF_{exec}$ and $TF_{conf}$.
- **Output:** Time-synchronized directional bias and trigger confirmation.
- **Owner:** Strategy Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Signals on $TF_{exec}$ are blocked if $TF_{conf}$ trend contradicts trade direction on the corresponding closed higher timeframe bar.
- **Failure Behavior:** Emits `SignalRejection(code="REJECT_HTF_NONCONFIRMATION")`.
- **Test Reference:** `tests/unit/strategy/test_multi_timeframe_alignment.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-004: Index and Futures Dual Confirmation
- **Description:** Strategy must require simultaneous directional confirmation between the underlying cash index and the front-month futures contract on closed candle `[1]`.
- **Source:** Master Specification Section 5 ("V1 Scope") & Section 11 ("Index-Futures Basis").
- **Input:** Simultaneous closed candle `[1]` for cash index spot and active futures contract.
- **Output:** Verified dual-confirmed `SignalIntent`.
- **Owner:** Strategy Engine & Basis Engine.
- **Priority:** P1.
- **Acceptance Criteria:** An entry signal is emitted ONLY IF both index spot and futures contract satisfy directional trigger rules on candle `[1]`.
- **Failure Behavior:** If futures do not confirm cash index direction, signal is rejected with `REJECT_FUTURES_NONCONFIRMATION`.
- **Test Reference:** `tests/unit/strategy/test_dual_confirmation.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-005: Deterministic Single Entry / Single Stop / Single Target
- **Description:** Every trade setup must possess exactly one entry order, one initial protective stop-loss order, and one profit target order.
- **Source:** Master Specification Section 5 ("V1 Scope").
- **Input:** Emitted `SignalIntent` with price structure.
- **Output:** Immutable trade structure containing exact entry price, stop-loss price, and target price.
- **Owner:** Strategy Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Verified exactly 1 entry, 1 stop-loss, and 1 target calculated. No multi-stage scaling or partial brackets in V1.
- **Failure Behavior:** If calculated stop or target is non-positive or invalid distance, signal is rejected with `REJECT_INVALID_BRACKET`.
- **Test Reference:** `tests/unit/strategy/test_trade_structure.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-006: Strict Exclusion of Prohibited Features
- **Description:** System must strictly forbid AI signal generation, ML scoring, news sentiment, averaging down, auto live scaling, and uncontrolled strategy switching.
- **Source:** Master Specification Section 5 ("Initially Excluded").
- **Input:** Codebase and configuration manifests.
- **Output:** Guaranteed absence of prohibited modules.
- **Owner:** Core Architecture.
- **Priority:** P0.
- **Acceptance Criteria:** Zero machine learning libraries in core dependencies; architectural assertions verify zero dynamic position scaling or averaging down paths.
- **Failure Behavior:** Architectural linter and pre-commit checks fail if non-approved dependencies or patterns are introduced.
- **Test Reference:** `tests/unit/core/test_architecture_boundaries.py`.
- **Status:** Frozen (Phase 0).

---

### 2.2 Data Governance & Market Data Subsystem

#### AF-REQ-007: Canonical Market Data Schema Preservation
- **Description:** Every market data record must preserve 15 mandatory fields without truncation or loss of precision.
- **Source:** Master Specification Section 9 ("Data Safety Rules").
- **Input:** Raw incoming exchange packets / historical archives.
- **Output:** Immutable `MarketCandle` record: `(symbol, instrument_type, contract_id, exchange_timestamp, received_timestamp, timeframe, open, high, low, close, volume, open_interest, source, quality_status, data_version)`.
- **Owner:** Data Ingestion Layer.
- **Priority:** P0.
- **Acceptance Criteria:** All 15 fields present, strongly typed with strict Decimal representation for prices.
- **Failure Behavior:** If any field is missing or invalid, record is marked `quality_status="CORRUPTED"` and rejected.
- **Test Reference:** `tests/unit/data/test_candle_schema.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-008: Market Data Invariant Validation
- **Description:** Strict boundary checks: $high \ge \max(open, close, low)$, $low \le \min(open, close, high)$, $volume \ge 0$, $OI \ge 0$, and strict timestamp monotonicity ($ts_{k+1} > ts_k$).
- **Source:** Master Specification Section 9 ("Data Safety Rules").
- **Input:** Parsed `MarketCandle`.
- **Output:** Validated candle passed to store, or immediate rejection.
- **Owner:** Data Quality & Normalization Layer.
- **Priority:** P0.
- **Acceptance Criteria:** 100% of candles passing validator satisfy all mathematical boundary assertions.
- **Failure Behavior:** Corrupt candle rejected with audit event `DATA_REJECTED_INVARIANT_BREACH`; candle never reaches strategy.
- **Test Reference:** `tests/property/test_candle_invariants.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-009: Fail-Closed Missing Candle & Gap Handling
- **Description:** Upon detecting a skipped interval or missing candle in historical sequence, system must immediately halt signal generation, log the gap, and trigger backfill.
- **Source:** Master Specification Section 9 ("Data Safety Rules").
- **Input:** Consecutive closed candles with timestamp delta exceeding expected timeframe interval.
- **Output:** Execution lock (`trading_permission=BLOCKED`), gap audit alert, and automated backfill request.
- **Owner:** Data Quality Layer & Engine Coordinator.
- **Priority:** P0.
- **Acceptance Criteria:** No trading signal generated during a detected data gap; resumption occurs only after revalidation of backfilled data.
- **Failure Behavior:** Emits `REJECT_DATA_GAP` and transitions engine state to `HALTED_DATA_GAP`.
- **Test Reference:** `tests/integration/data/test_gap_detection.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-010: Stale Data Rejection Gate
- **Description:** Market data feeds must be verified for freshness. Stale data must never trigger execution.
- **Source:** Master Specification Section 9 ("Data Safety Rules").
- **Input:** Incoming ticks/candles with exchange timestamp compared against reference wall clock / max allowed delay.
- **Output:** Validated fresh data, or rejection.
- **Owner:** Data Quality Layer.
- **Priority:** P1.
- **Acceptance Criteria:** Candle with latency exceeding threshold is blocked from triggering signals.
- **Failure Behavior:** Emits `SignalRejection(code="REJECT_DATA_STALE")`.
- **Test Reference:** `tests/unit/data/test_stale_data_rejection.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-011: Duplicate Data Collision Handling
- **Description:** Duplicate candle records on composite key `(symbol, contract_id, timeframe, exchange_timestamp)` must be detected.
- **Source:** Master Specification Section 9 ("Data Safety Rules").
- **Input:** Ingested candle with an identical composite key to an existing validated candle.
- **Output:** Deduplicated stream; collision alert.
- **Owner:** Data Ingestion & Store.
- **Priority:** P1.
- **Acceptance Criteria:** Identical duplicate packets silently discarded; differing duplicate packets trigger `DATA_ANOMALY_COLLISION`. Existing data is never blindly overwritten.
- **Failure Behavior:** Quarantines conflicting record and logs audit alert.
- **Test Reference:** `tests/unit/data/test_duplicate_handling.py`.
- **Status:** Frozen (Phase 0).

---

### 2.3 Contract Master & Basis Subsystem

#### AF-REQ-012: Deterministic Contract Lifecycle & Selection
- **Description:** Active futures contract must be selected deterministically based on contract status, liquidity, days to expiry, and rollover schedule.
- **Source:** Master Specification Section 10 ("Contract Lifecycle").
- **Input:** Current trading date/time, instrument master registry, and expiry rules.
- **Output:** Active front-month contract identifier and exact lot size.
- **Owner:** Contract Master.
- **Priority:** P1.
- **Acceptance Criteria:** Correct front-month contract selected for any historical or live date; rollover executed deterministically.
- **Failure Behavior:** If contract is expired or within blackout window, trade is rejected with `REJECT_INVALID_CONTRACT`.
- **Test Reference:** `tests/unit/contract/test_contract_lifecycle.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-013: Index-Futures Basis Calculation & Anomaly Gate
- **Description:** System must compute basis points ($P_{fut} - P_{spot}$), basis %, and basis z-score, rejecting trades during statistical anomalies.
- **Source:** Master Specification Section 11 ("Index-Futures Basis").
- **Input:** Simultaneous prices of underlying cash index and front-month futures.
- **Output:** Continuous basis metrics and validation status.
- **Owner:** Basis Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Trades rejected when basis z-score exceeds configured standard deviations.
- **Failure Behavior:** Emits `SignalRejection(code="REJECT_BASIS_ABNORMAL")`.
- **Test Reference:** `tests/unit/basis/test_basis_engine.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-014: Index-Futures Timestamp Desynchronization Gate
- **Description:** Timestamps between cash index and futures feeds must not drift beyond maximum permissible threshold.
- **Source:** Master Specification Section 11 ("Index-Futures Basis").
- **Input:** Exchange timestamps of latest received index and futures ticks/candles.
- **Output:** Synchronization status (In-Sync vs Desynchronized).
- **Owner:** Basis Engine & Signal Validator.
- **Priority:** P1.
- **Acceptance Criteria:** If $|\Delta ts| > \text{threshold}$, trading signals are immediately vetoed.
- **Failure Behavior:** Emits `SignalRejection(code="REJECT_INDEX_FUTURES_DESYNC")`.
- **Test Reference:** `tests/unit/basis/test_timestamp_desync.py`.
- **Status:** Frozen (Phase 0).

---

### 2.4 Independent Risk Subsystem

#### AF-REQ-015: Independent Risk Gatekeeper with Unilateral Veto
- **Description:** The Risk Engine must be completely decoupled from strategy logic and hold absolute, unbypassable veto authority over all order intents.
- **Source:** Master Specification Section 3 ("Risk Independence") & Section 7 ("Responsibility Boundaries").
- **Input:** `SignalIntent` proposed by Strategy Engine + current account risk state.
- **Output:** Approved `RiskIntent` or hard `RiskRejection`.
- **Owner:** Risk Engine.
- **Priority:** P0.
- **Acceptance Criteria:** No execution decision can be generated without explicit Risk Engine cryptographic approval.
- **Failure Behavior:** Unapproved signals are permanently dropped; audit event `RISK_REJECTED` logged.
- **Test Reference:** `tests/unit/risk/test_risk_independence.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-016: Maximum Daily Loss Hard Stop Limit
- **Description:** Trading must immediately lock out for the remainder of the session if realized + unrealized losses reach or exceed the configured maximum daily loss.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Cumulative session realized PnL + mark-to-market unrealized PnL.
- **Output:** System risk state: `NORMAL` vs `DAILY_LOSS_LOCKOUT`.
- **Owner:** Risk Engine.
- **Priority:** P0.
- **Acceptance Criteria:** Once breached, all open limit orders cancelled, active positions managed to exit, and 100% of new entries blocked until session reset.
- **Failure Behavior:** Rejects order with `REJECT_RISK_DAILY_LOSS_BREACH` and triggers P0 notification.
- **Test Reference:** `tests/unit/risk/test_daily_loss_circuit_breaker.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-017: Maximum Open Risk Exposure Ceiling
- **Description:** Cumulative capital at risk across all open positions ($\sum \text{StopDistance} \times \text{Quantity}$) must never exceed configured open risk ceiling.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Proposed new trade stop distance + existing open positions risk sum.
- **Output:** Approval or rejection of proposed risk.
- **Owner:** Risk Engine.
- **Priority:** P0.
- **Acceptance Criteria:** Proposed trade rejected if adding it would cause total open risk to exceed maximum ceiling.
- **Failure Behavior:** Emits `RiskRejection(code="REJECT_MAX_OPEN_RISK_EXCEEDED")`.
- **Test Reference:** `tests/unit/risk/test_open_risk_ceiling.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-018: Lot-Size-Aware Position Sizing Math
- **Description:** Position quantity must be calculated as discrete integer multiples of exchange lot size: $\text{allowed\_lots} = \lfloor \frac{\text{Capital} \times r}{\text{StopDistance} \times \text{LotSize}} \rfloor$.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Account equity, risk fraction $r$, stop-loss distance, and contract lot size.
- **Output:** Exact integer lot allocation.
- **Owner:** Risk Engine.
- **Priority:** P0.
- **Acceptance Criteria:** If $\text{allowed\_lots} < 1$, the trade is strictly rejected. The system must NEVER round up to 1 lot.
- **Failure Behavior:** Rejection with explicit code `REJECT_RISK_BELOW_MINIMUM_LOT`.
- **Test Reference:** `tests/unit/risk/test_lot_size_math.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-019: Absolute Prohibition of Averaging Down & Martingale
- **Description:** Sizing must never increase following a losing trade. System must strictly forbid averaging down on adverse position excursion.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Trade history sequence + incoming order intent.
- **Output:** Invariant verification assertion.
- **Owner:** Risk Engine.
- **Priority:** P0.
- **Acceptance Criteria:** Order quantity for trade $t$ cannot exceed trade $t-1$ following a loss; scale-in orders on losing positions are impossible.
- **Failure Behavior:** Hard assertion failure, immediate audit alarm, and rejection with `REJECT_AVERAGING_DOWN_PROHIBITED`.
- **Test Reference:** `tests/unit/risk/test_anti_martingale_invariant.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-020: Exchange-Session Boundary Daily Risk Reset
- **Description:** Daily risk counters and cumulative loss limits must reset strictly at the configured exchange session boundary (e.g. 09:15 IST), NEVER at local system midnight.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Exchange session calendar and market hours configuration.
- **Output:** Session start/end events driving risk accumulator lifecycle.
- **Owner:** Risk Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Risk counters remain persistent across local midnight and reset only when official session opens.
- **Failure Behavior:** Misaligned system clock or invalid session definition prevents risk reset and blocks trading.
- **Test Reference:** `tests/unit/risk/test_session_boundary_reset.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-021: Manual Broker Position Accounting
- **Description:** Exposure calculations must include manual positions entered directly on broker console outside AlphaForge.
- **Source:** Master Specification Section 12 ("Risk Invariants").
- **Input:** Reconciled broker positions list containing manual/external positions.
- **Output:** Total consolidated account exposure.
- **Owner:** Risk Engine & Reconciliation Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Manual positions reduce available margin and open risk capacity for automated trading.
- **Failure Behavior:** If manual position breaches total risk ceiling, automated trading immediately locks entries.
- **Test Reference:** `tests/integration/risk/test_manual_position_accounting.py`.
- **Status:** Frozen (Phase 0).

---

### 2.5 Cost & Execution Subsystem

#### AF-REQ-022: Realistic Statutory & Exchange Transaction Cost Model
- **Description:** Transaction cost model must deduct exact statutory exchange charges (Securities Transaction Tax, Exchange Turnover Fee, SEBI Turnover Fee, Stamp Duty, GST, and Brokerage) on every trade.
- **Source:** Master Specification Section 5 ("V1 Scope").
- **Input:** Executed price, lot size, quantity, transaction type (BUY/SELL).
- **Output:** Detailed breakdown of statutory charges and net realized PnL.
- **Owner:** Cost & Slippage Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Calculated charges match broker contract notes within $\pm 0.01\%$.
- **Failure Behavior:** If tariff parameters are unconfigured, backtesting and live simulation fail closed.
- **Test Reference:** `tests/unit/cost/test_exchange_cost_model.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-023: Volatility-Dependent Slippage Modeling
- **Description:** Backtest and simulation engines must apply realistic, volatility-dependent bid-ask spread and impact slippage.
- **Source:** Master Specification Section 5 ("V1 Scope").
- **Input:** Market candle ATR, bid-ask spread, order lot size, and execution type (MARKET/LIMIT).
- **Output:** Simulated fill price penalized by modeled slippage.
- **Owner:** Cost & Slippage Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Zero slippage is never assumed. Slippage scales realistically during high-volatility candles.
- **Failure Behavior:** Missing volatility features trigger conservative fallback slippage penalty.
- **Test Reference:** `tests/unit/cost/test_slippage_model.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-024: Formal 17-State Order Lifecycle Machine
- **Description:** Order execution must strictly follow a deterministic 17-state finite state machine with guarded legal transitions only.
- **Source:** Master Specification Section 13 ("Order State Machine").
- **Input:** State transition requests triggered by internal engine events or broker execution reports.
- **Output:** Validated state mutation + audit event.
- **Owner:** Order State Machine.
- **Priority:** P0.
- **Acceptance Criteria:** Attempting any invalid state transition raises an immediate exception and halts order mutation.
- **Failure Behavior:** Emits `IllegalStateTransitionError`, transitions order to `MANUAL_ESCALATION`, and triggers P0 alert.
- **Test Reference:** `tests/unit/execution/test_state_machine.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-025: Emergency Unprotected Position Protocol
- **Description:** A filled position without a confirmed active protective stop-loss order within the maximum timeout window must trigger the emergency protocol.
- **Source:** Master Specification Section 14 ("Unprotected Position").
- **Input:** Order in `FILLED` state entering `PROTECTION_PENDING` without broker stop confirmation within timeout threshold.
- **Output:** Immediate block on new entries, P0 emergency alert, broker position query, approved emergency market exit, and escalation.
- **Owner:** Execution Decision Engine & Safety Watchdog.
- **Priority:** P0.
- **Acceptance Criteria:** Under simulated stop-loss rejection or timeout, system halts new entries and dispatches emergency market close order.
- **Failure Behavior:** If market close fails, transitions state to `MANUAL_ESCALATION` and activates audio/pager sirens.
- **Test Reference:** `tests/integration/execution/test_unprotected_position_emergency.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-026: Deterministic Client Order ID Idempotency
- **Description:** Every logical order submission must possess a deterministic, uniquely reproducible client order ID.
- **Source:** Master Specification Section 15 ("Idempotency").
- **Input:** Strategy identity, strategy version, signal identity, and attempt sequence number.
- **Output:** Unique deterministic string `client_order_id`.
- **Owner:** Execution Engine.
- **Priority:** P0.
- **Acceptance Criteria:** Re-submitting the same signal or retrying after network timeout uses the identical ID; broker deduplicates without double fills.
- **Failure Behavior:** Never generate a fresh ID on network timeout; must reconcile prior ID before any action.
- **Test Reference:** `tests/unit/execution/test_idempotency.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-027: Cold-Boot 9-Step Crash Recovery Protocol
- **Description:** On system startup or process reboot, AlphaForge must execute the mandatory 9-step sequence before unlocking trading.
- **Source:** Master Specification Section 16 ("Crash Recovery").
- **Input:** Local database records + live broker API order/position book.
- **Output:** Fully reconciled internal state machine with verified active stop-loss orders.
- **Owner:** Reconciliation Engine.
- **Priority:** P0.
- **Acceptance Criteria:** 1. Load local orders; 2. Load local positions; 3. Fetch broker orders; 4. Fetch broker positions; 5. Compare states; 6. Reconcile differences; 7. Keep entries blocked; 8. Verify protection; 9. Resume only after success.
- **Failure Behavior:** Any unresolved position mismatch or missing stop keeps trading permanently locked and transitions to `MANUAL_ESCALATION`.
- **Test Reference:** `tests/integration/reconciliation/test_crash_recovery_9step.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-028: Runtime Broker Reconciliation Loop
- **Description:** Local positions and open orders must be continuously cross-compared against broker reports to detect ghost orders, execution lags, or external modifications.
- **Source:** Master Specification Section 3 ("Reconciliation") & Section 7 ("Responsibility Boundaries").
- **Input:** Periodic broker polling / WebSocket execution events.
- **Output:** State synchronization status; mismatch delta list.
- **Owner:** Reconciliation Engine.
- **Priority:** P0.
- **Acceptance Criteria:** Any discrepancy between local position quantity and broker position quantity blocks new entries within 1 cycle.
- **Failure Behavior:** Emits `RECONCILIATION_MISMATCH_ALARM` and freezes execution decision engine.
- **Test Reference:** `tests/unit/reconciliation/test_runtime_reconciler.py`.
- **Status:** Frozen (Phase 0).

---

### 2.6 Audit, Validation, Security & Operations

#### AF-REQ-029: Immutable Tamper-Evident Audit Ledger
- **Description:** Every data rejection, feature calculation, signal, risk approval/veto, order submission, fill, and reconciliation event must produce an immutable audit event with cryptographic hash chaining.
- **Source:** Master Specification Section 17 ("Audit Ledger").
- **Input:** Domain event payload + hash of previous ledger record.
- **Output:** Persisted audit record with `previous_event_hash` and `event_hash`.
- **Owner:** Audit Ledger.
- **Priority:** P1.
- **Acceptance Criteria:** Full sequence of system decisions can be reconstructed; modifying any historical record breaks the hash chain validation.
- **Failure Behavior:** If ledger persistence fails, trading halts immediately (`HALTED_PERSISTENCE_FAILURE`).
- **Test Reference:** `tests/unit/audit/test_tamper_evident_ledger.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-030: Deterministic Historical Replay Engine
- **Description:** System must support offline replay of recorded market data producing bit-exact signals, rejections, risk choices, and order intents without relying on wall-clock time.
- **Source:** Master Specification Section 19 ("Replay").
- **Input:** Historical recorded market data fixtures + versioned config.
- **Output:** Replayed event log bit-identical to live recording.
- **Owner:** Replay Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Replay produces identical hash chain and order states as recorded session.
- **Failure Behavior:** Replay divergence flags regression defect in system determinism.
- **Test Reference:** `tests/integration/replay/test_deterministic_replay.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-031: Quantitative Validation Pipeline
- **Description:** Strategy must be subjected to In-Sample, Out-of-Sample, Walk-Forward, Monte Carlo, and cost stress testing before being classified as `VALIDATED`.
- **Source:** Master Specification Section 18 ("Quant Validation").
- **Input:** Multi-year partitioned market data + strategy specification.
- **Output:** Formal statistical score card: Expectancy, Profit Factor, Max Drawdown, Payoff Ratio, Risk of Ruin, and Status (`VALIDATED`, `INCONCLUSIVE`, `REJECTED`).
- **Owner:** Quant Validation Engine.
- **Priority:** P1.
- **Acceptance Criteria:** Strategy achieving $PF < 1.5$ or sample size $< 150$ trades is rejected or marked inconclusive.
- **Failure Behavior:** Failed validation permanently blocks promotion to Paper or Live phases.
- **Test Reference:** `tests/integration/quant/test_validation_pipeline.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-032: 17-Scenario Failure Injection Suite
- **Description:** Automated test suite must inject and verify all 17 failure modes defined in Master Specification Section 20.
- **Source:** Master Specification Section 20 ("Failure Injection").
- **Input:** Chaos test harness simulating network drops, stale feeds, broker timeouts, partial fills, clock drifts, and process crashes.
- **Output:** Verified fail-closed behavior, alert emissions, and state recovery.
- **Owner:** Reliability & QA Suite.
- **Priority:** P0.
- **Acceptance Criteria:** 17 out of 17 chaos test scenarios pass with zero unhandled exceptions and zero capital leakage.
- **Failure Behavior:** Any scenario resulting in unmonitored risk fails the test suite.
- **Test Reference:** `tests/failure_injection/test_chaos_suite.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-033: Zero-Trust Security & Credential Isolation
- **Description:** Absolute prohibition of hardcoded API keys, tokens, or credentials in source code. Paper and live credentials must be physically isolated.
- **Source:** Master Specification Section 21 ("Security").
- **Input:** Environment configurations and secrets storage.
- **Output:** Sanitized logs and securely injected runtime configurations.
- **Owner:** Security Layer.
- **Priority:** P0.
- **Acceptance Criteria:** Secret scanners confirm zero token signatures in git history; all secrets injected exclusively via environment variables.
- **Failure Behavior:** Pre-commit hooks block any commit containing token signatures.
- **Test Reference:** `tests/unit/security/test_credential_isolation.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-034: Controlled Environment Promotion Hierarchy
- **Description:** Trading execution is strictly gated across Research $\to$ Development $\to$ Paper $\to$ Shadow $\to$ Tiny Live $\to$ Production.
- **Source:** Master Specification Section 3 ("Controlled Promotion") & Section 23 ("Environment Separation").
- **Input:** Deployment environment flag (`ENVIRONMENT`).
- **Output:** Enforced execution permissions (`LIVE_TRADING = FALSE` by default).
- **Owner:** Core Architecture & Configuration.
- **Priority:** P0.
- **Acceptance Criteria:** Real broker order placement throws hard assertion error unless environment is explicitly `TINY_LIVE` or `PRODUCTION` with signed approval.
- **Failure Behavior:** Unauthorized live attempt triggers immediate process shutdown.
- **Test Reference:** `tests/unit/core/test_environment_guards.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-035: Operational Telemetry & Multi-Channel Kill Switch
- **Description:** Continuous monitoring of data freshness, latency, drift, and health heartbeats, coupled with automated and manual emergency kill switches.
- **Source:** Master Specification Section 22 ("Observability") & Section 5 ("V1 Scope").
- **Input:** Heartbeat signals, latency metrics, and kill-switch trigger.
- **Output:** Live operational metrics, P0 emergency notifications, and instant execution freeze.
- **Owner:** Monitoring & Alerting Layer.
- **Priority:** P1.
- **Acceptance Criteria:** Kill switch activation cancels pending orders, manages positions, and transitions system to `HALTED_MANUAL_INTERVENTION`.
- **Failure Behavior:** Failed heartbeat triggers automated circuit breaker.
- **Test Reference:** `tests/integration/monitoring/test_kill_switch.py`.
- **Status:** Frozen (Phase 0).

#### AF-REQ-036: Semantic Versioning & Behavior Change Control
- **Description:** Every change affecting signals, risk, contract selection, or execution must be tracked with semantic versioning and cryptographic manifest hashes.
- **Source:** Master Specification Section 24 ("Change Control").
- **Input:** Strategy version, configuration hash, data version, and risk policy version.
- **Output:** Versioned metadata attached to every trade decision and audit record.
- **Owner:** Core Architecture.
- **Priority:** P1.
- **Acceptance Criteria:** Changes classified as PATCH, MINOR, or MAJOR; MAJOR changes require re-running the full validation pipeline.
- **Failure Behavior:** Uncommitted or unversioned configurations reject live startup.
- **Test Reference:** `tests/unit/core/test_change_control.py`.
- **Status:** Frozen (Phase 0).
