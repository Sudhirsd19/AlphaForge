# ALPHAFORGE — MASTER DEVELOPMENT ROADMAP (AUTHORITATIVE PHASES 0 TO 17)

**Project Name:** AlphaForge  
**Author:** Principal Software Architect, Senior Quantitative Trading Systems Engineer  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan (Master Specification)

---

## 1. Authoritative Phase Sequence

The development sequence of AlphaForge is permanently locked to the 18 phases defined in the Master Specification. No phase numbering or sequencing may be altered:

```text
Phase 0  = Requirement Freeze
Phase 1  = Deterministic Strategy Specification
Phase 2  = Data Governance and Market Data Layer
Phase 3  = Futures and Contract Lifecycle Engine
Phase 4  = Index-Futures Basis Engine
Phase 5  = Risk Engine
Phase 6  = Cost and Slippage Model
Phase 7  = Order State Machine
Phase 8  = Idempotency and Crash Recovery
Phase 9  = Audit Ledger
Phase 10 = Backtest and Quant Validation
Phase 11 = Replay Engine
Phase 12 = Failure-Injection Testing
Phase 13 = Security and Compliance
Phase 14 = Observability
Phase 15 = Deployment Environments
Phase 16 = Paper / Shadow
Phase 17 = Controlled Live
```

*Note on Supporting Infrastructure:* Toolchain components (Git, `pyproject.toml`, dependency lockfiles, Ruff, mypy, pytest, CI configs) are strictly supporting infrastructure for Phase 0 and do not alter or redefine the phase numbering.

---

### Phase 0: Requirement Freeze
- **Objective:** Convert the Master Specification into an implementation-ready formal contract. Freeze scope, invariants, acceptance criteria, and failure behaviors.
- **Dependencies:** None.
- **Files/Modules:** `docs/REQUIREMENT_FREEZE.md`, `docs/FORMAL_REQUIREMENT_MATRIX.md`, `docs/OPEN_DESIGN_DECISIONS.md`, `docs/ACCEPTANCE_GATES.md`, supporting config (`pyproject.toml`, `.gitignore`, `README.md`).
- **Implementation Tasks:** Codify all functional and safety requirements into the formal matrix; catalogue open technical decisions separating mandatory requirements from design proposals; set up supporting tooling infrastructure.
- **Tests:** Verify documentation consistency, schema completeness, and tooling configuration.
- **Acceptance Criteria:** Master Specification formally mapped; zero silent modifications; scope frozen; acceptance gates defined for all 18 phases; open design choices identified.
- **Exit Gate:** Explicit user approval command: `"PROCEED TO PHASE 1"`.
- **Risks:** Unresolved ambiguities; mitigated by explicit open decision catalogue.

---

### Phase 1: Deterministic Strategy Specification
- **Objective:** Codify the single deterministic multi-timeframe strategy as pure mathematical functions without lookahead bias.
- **Dependencies:** Phase 0.
- **Files/Modules:** `alphaforge/core/models.py`, `alphaforge/strategy/*`, `tests/golden/*`.
- **Implementation Tasks:** Build pure vectorized indicator library, implement entry/stop/target signal logic, enforce closed-candle quarantine `[1]` vs `[0]`.
- **Tests:** Golden fixture unit tests; Property-based test (`hypothesis`) proving mutating `[0]` has 0 effect on signal output.
- **Acceptance Criteria:** Identical input candle series produces 100% bit-exact signal output.
- **Exit Gate:** 100% passing tests on all golden fixtures; user sign-off.
- **Risks:** Hidden statefulness in indicators; mitigated by pure functional design.

---

### Phase 2: Data Governance and Market Data Layer
- **Objective:** Implement fail-closed market data ingestion pipeline with strict invariant checking.
- **Dependencies:** Phase 1.
- **Files/Modules:** `alphaforge/data/models.py`, `alphaforge/data/validator.py`, `alphaforge/data/resampler.py`.
- **Implementation Tasks:** Implement `MarketCandle` Pydantic model, OHLC boundary assertions, timestamp monotonicity guards, and gap detector with backfill hook.
- **Tests:** Fuzz testing with corrupt OHLC, negative volume, reversed timestamps, and skipped candles.
- **Acceptance Criteria:** Any malformed, stale ($>15s$), or non-monotonic candle immediately halts processing with an explicit audit code.
- **Exit Gate:** 100% gap detection and corruption rejection in automated tests; user sign-off.
- **Risks:** Performance overhead of validation; mitigated by vectorized pre-checks.

---

### Phase 3: Futures and Contract Lifecycle Engine
- **Objective:** Implement deterministic instrument lookup, lot-size tracking, and expiry rollover logic.
- **Dependencies:** Phase 2.
- **Files/Modules:** `alphaforge/core/contract_master.py`, `config/instruments.yaml`.
- **Implementation Tasks:** Build `ContractMaster` handling front-month selection, days-to-expiry tracking, rollover blackout windows, and lot size lookup.
- **Tests:** Expiry calendar boundary tests; ensure trades are blocked within 2 days of expiry.
- **Acceptance Criteria:** Contract master deterministically selects correct trading symbol for any arbitrary timestamp.
- **Exit Gate:** Automated tests verify seamless simulated monthly contract rollover; user sign-off.
- **Risks:** Hardcoded expiry dates; mitigated by exchange holiday calendar integration.

---

### Phase 4: Index-Futures Basis Engine
- **Objective:** Implement dual-feed synchronization and basis validation between Index Spot and Futures.
- **Dependencies:** Phase 3.
- **Files/Modules:** `alphaforge/indicators/basis.py`, `alphaforge/strategy/basis_validator.py`.
- **Implementation Tasks:** Compute Basis points ($P_{fut} - P_{spot}$), basis %, and rolling basis z-score. Implement timestamp desync gate ($|\Delta ts| > \text{threshold}$).
- **Tests:** Feed desynchronization simulation; abnormal basis spike injection.
- **Acceptance Criteria:** Signals rejected with `REJECT_BASIS_ABNORMAL` or `REJECT_INDEX_FUTURES_DESYNC` when thresholds are exceeded.
- **Exit Gate:** Basis engine reliably blocks trades during unconfirmed spot/futures divergence; user sign-off.
- **Risks:** False positives during cash market open; mitigated by warm-up period.

---

### Phase 5: Risk Engine
- **Objective:** Implement decoupled Risk Engine enforcing daily loss limits, open risk ceilings, and discrete lot sizing.
- **Dependencies:** Phase 4.
- **Files/Modules:** `alphaforge/risk/*`.
- **Implementation Tasks:** Implement discrete lot formula $\lfloor \frac{\text{Capital} \times r}{\Delta P \times \text{LotSize}} \rfloor$, daily loss tracker with exchange session reset boundary, and open risk aggregator.
- **Tests:** Boundary tests for capital too small for 1 lot (`REJECT_RISK_BELOW_MINIMUM_LOT`), daily loss lockout simulation.
- **Acceptance Criteria:** Sizing never violates risk capital; zero Martingale or scale-in allowed.
- **Exit Gate:** 100% test coverage on all risk invariants; user sign-off.
- **Risks:** Exchange margin changes; mitigated by conservative 20% margin buffer.

---

### Phase 6: Cost and Slippage Model
- **Objective:** Model exact exchange statutory charges (STT, stamp duty, turnover fees, GST, brokerage) and market slippage.
- **Dependencies:** Phase 5.
- **Files/Modules:** `alphaforge/execution/cost_model.py`.
- **Implementation Tasks:** Build Indian exchange futures tariff engine and volatility-dependent bid-ask spread slippage calculator.
- **Tests:** Cross-reconcile computed trade costs against official broker contract notes down to the rupee.
- **Acceptance Criteria:** Backtest engine deducts realistic friction on every simulated fill.
- **Exit Gate:** Cost model discrepancy $< 0.01\%$ vs actual exchange tariff sheets; user sign-off.
- **Risks:** Statutory tax rate adjustments; mitigated by externalized YAML configuration.

---

### Phase 7: Order State Machine
- **Objective:** Implement formal 17-state FSM and the Emergency Unprotected Position Protocol.
- **Dependencies:** Phase 6.
- **Files/Modules:** `alphaforge/execution/state_machine.py`, `alphaforge/execution/protection_watchdog.py`.
- **Implementation Tasks:** Implement state transitions, transition guards, and Stop-Loss watchdog.
- **Tests:** Unit test full transition matrix; simulate dropped SL ACK to trigger emergency market exit.
- **Acceptance Criteria:** No order can exist in an illegal state; unprotected position immediately halts engine and forces exit.
- **Exit Gate:** Emergency watchdog successfully aborts trades within target window under simulated failure; user sign-off.
- **Risks:** Race conditions between fill event and stop dispatch; mitigated by atomic FSM transitions.

---

### Phase 8: Idempotency and Crash Recovery
- **Objective:** Build deterministic `client_order_id` generator, abstract broker interface, and 9-step cold-boot reconciler.
- **Dependencies:** Phase 7.
- **Files/Modules:** `alphaforge/broker/*`, `alphaforge/reconciliation/*`, `alphaforge/execution/idempotency.py`.
- **Implementation Tasks:** Implement deterministic order keys, Simulated Paper Broker, and startup reconciliation loop querying broker orders/positions.
- **Tests:** Simulate mid-trade process kill (`SIGKILL`); restart engine and verify state correctly re-aligned from persistence + broker.
- **Acceptance Criteria:** Zero duplicate orders emitted under duplicate calls or network retries; 100% recovery after crash.
- **Exit Gate:** Passing automated crash-recovery test suite; user sign-off.
- **Risks:** Broker API rate limits during boot; mitigated by paced token-bucket rate limiter.

---

### Phase 9: Audit Ledger
- **Objective:** Implement append-only audit store with cryptographic hash chaining.
- **Dependencies:** Phase 8.
- **Files/Modules:** `alphaforge/audit/ledger.py`, `alphaforge/audit/models.py`.
- **Implementation Tasks:** Schema for audit events, cryptographic chain verification (`previous_event_hash` $\to$ `event_hash`).
- **Tests:** Tamper detection test: mutate historical row; assert integrity verification fails immediately.
- **Acceptance Criteria:** Every trade intent, rejection, risk evaluation, and order transition is permanently immutably recorded.
- **Exit Gate:** Audit ledger passes cryptographic tamper-proofing validation; user sign-off.
- **Risks:** Disk I/O bottlenecks; mitigated by WAL mode and async batching.

---

### Phase 10: Backtest and Quant Validation
- **Objective:** Build backtest runner with Walk-Forward Analysis, Monte Carlo resampling, and parameter perturbation.
- **Dependencies:** Phase 9.
- **Files/Modules:** `alphaforge/quant/*`, `alphaforge/backtest/*`.
- **Implementation Tasks:** Event-driven backtester using historical Parquet candles, WFA window engine, Monte Carlo 1,000-run simulation.
- **Tests:** Verify backtest results match tick-by-tick replay bit-for-bit.
- **Acceptance Criteria:** Full statistical report generated (Expectancy, Profit Factor, Drawdown, Payoff Ratio, Risk of Ruin).
- **Exit Gate:** Strategy formally classified as `VALIDATED`, `INCONCLUSIVE`, or `REJECTED`; user sign-off.
- **Risks:** Overfitting to noise; mitigated by strict Walk-Forward out-of-sample hurdles.

---

### Phase 11: Replay Engine
- **Objective:** Enable exact historical simulation and post-incident investigation via offline tick/candle replay.
- **Dependencies:** Phase 10.
- **Files/Modules:** `alphaforge/replay/*`.
- **Implementation Tasks:** Build virtual clock and fixture replay engine feeding recorded data into unchanged live pipeline.
- **Tests:** Replay recorded trading day; verify identical signals, rejections, order states, and ledger hashes.
- **Acceptance Criteria:** Replay produces 100% reproducible state without external API dependencies.
- **Exit Gate:** Bit-exact replay verified across 5 distinct historical trading sessions; user sign-off.
- **Risks:** Leaking system wall-clock time; mitigated by injecting abstract `TimeProvider`.

---

### Phase 12: Failure-Injection Testing
- **Objective:** Execute automated adversarial tests across data corruption, audit tampering, broker timeouts, process crashes, checkpoint corruption, storage I/O failures, clock anomalies, concurrency races, and risk-state persistence.
- **Dependencies:** Phase 11.
- **Files/Modules:** `alphaforge/fault_injection/*`, `tests/unit/fault_injection/*`, `tests/property/test_fault_injection_properties.py`, `docs/PHASE_12_FAILURE_INJECTION.md`.
- **Implementation Tasks:** Build isolated deterministic fault injection module (`models.py`, `injectors.py`, `invariants.py`), implement unit failure suites (D1–D6, L1–L8, O1–O9, C1–C8, K1–K5, S1–S5, T1–T5, RACE1–RACE5, Risk-1..9), golden adversarial scenarios (GOLDEN-1..6), and property tests (FP1–FP10).
- **Tests:** 112 deterministic tests (95 unit failure scenarios, 7 golden adversarial scenarios, 10 property tests).
- **Acceptance Criteria:** System gracefully halts, fails closed, reconciles, and reports alerts across all scenarios without capital leakage, state fabrication, or duplicate execution.
- **Exit Gate:** 112/112 failure-injection tests passing; 708/708 full suite passing; zero live exchange or credentials leaks; formal documentation complete; user sign-off.
- **Status:** IMPLEMENTED / VALIDATED (Freeze Ready).
- **Risks:** Incomplete test harness isolation; mitigated by isolated `alphaforge/fault_injection/` module with zero modifications to production domain logic.

---

### Phase 13: Security and Compliance
- **Objective:** Harden application security, enforce zero-trust credentials, and eliminate secret leakage risks.
- **Dependencies:** Phase 12.
- **Files/Modules:** `config/settings.py`, `.gitignore`, `alphaforge/security/*`.
- **Implementation Tasks:** Pydantic settings validation, pre-commit secret scanners, redaction filters in loggers.
- **Tests:** Automated repository scan for token signatures; verify dummy keys used in non-live modes.
- **Acceptance Criteria:** Zero secrets present in codebase; live trading mode strictly locked behind authorization keys.
- **Exit Gate:** Clean security audit report and zero secret leaks; user sign-off.
- **Risks:** Accidental debug print of headers; mitigated by log redact filters.

---

### Phase 14: Observability
- **Objective:** Implement real-time latency monitoring, health heartbeats, and emergency alerting.
- **Dependencies:** Phase 13.
- **Files/Modules:** `alphaforge/monitoring/*`.
- **Implementation Tasks:** Feed latency trackers, memory/CPU monitors, order-to-ack latency timers, alert notifier, and manual emergency kill-switch CLI.
- **Tests:** Trigger simulated P0 emergency; verify alert arrives on notification channel within 2 seconds.
- **Acceptance Criteria:** Real-time health metrics exposed; manual kill switch immediately freezes system.
- **Exit Gate:** Alerting and kill switch verified end-to-end; user sign-off.
- **Risks:** Notification API outages; mitigated by redundant local emergency alarms.

---

### Phase 15: Deployment Environments
- **Objective:** Configure environment isolation across Research, Paper, Shadow, Tiny Live, and Production.
- **Dependencies:** Phase 14.
- **Files/Modules:** `deploy/*`, runtime configuration files.
- **Implementation Tasks:** Environment configuration manifests, reproducible runtime, NTP time synchronization service.
- **Tests:** Verify container builds and NTP synchronization ($<50\text{ms}$ drift).
- **Acceptance Criteria:** Strict separation of environment configurations verified.
- **Exit Gate:** Verified staging runtime environment ready for paper execution; user sign-off.
- **Risks:** Host OS clock drift; mitigated by continuous NTP daemon.

---

### Phase 16: Paper / Shadow
- **Objective:** Run continuous paper trading against real-time live market feeds to validate execution latency and fill dynamics.
- **Dependencies:** Phase 15.
- **Files/Modules:** `alphaforge/modes/paper_runner.py`, `alphaforge/modes/shadow_runner.py`.
- **Implementation Tasks:** Connect live WebSocket feeds to simulated matching engine; log all signals, hypothetical fills, and slippage.
- **Tests:** Minimum 15 consecutive trading days of continuous zero-crash operation in paper mode.
- **Acceptance Criteria:** Reconciled theoretical paper results against real exchange order books with zero state machine anomalies.
- **Exit Gate:** 15 trading days completed with 100% uptime and verified zero unhandled exceptions; user sign-off.
- **Risks:** Feed disconnects during market opening bell; verified handled by auto-reconnect.

---

### Phase 17: Controlled Live
- **Objective:** Deploy single-lot live trading with minimal capital allocation under strict human supervision.
- **Dependencies:** Phase 16.
- **Files/Modules:** Live execution configuration (`LIVE_TRADING = TRUE`).
- **Implementation Tasks:** Connect live broker API with 1-lot maximum risk cap, manual supervision desk, and continuous reconciliation.
- **Tests:** Real broker live order placement, execution, stop-loss confirmation, and position close for 1 lot.
- **Acceptance Criteria:** 100% successful broker order acknowledgments, verified stop-loss resting on exchange book, bit-exact reconciliation with broker console.
- **Exit Gate:** Final production audit sign-off by Principal Architect.
- **Risks:** Real financial capital risk; mitigated by hardcoded 1-lot limit and strict daily loss stop.
