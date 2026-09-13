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
- **Tests:** 114 deterministic tests (97 unit failure scenarios, 7 golden adversarial scenarios, 10 property tests).
- **Acceptance Criteria:** System gracefully halts, fails closed, reconciles, and reports alerts across all scenarios without capital leakage, state fabrication, or duplicate execution.
- **Exit Gate:** 114/114 failure-injection tests passing; 710/710 full suite passing; zero live exchange or credentials leaks; formal documentation complete; user sign-off.
- **Status:** IMPLEMENTED / VALIDATED (Freeze Ready).
- **Risks:** Incomplete test harness isolation; mitigated by isolated `alphaforge/fault_injection/` module with zero modifications to production domain logic.

---

### Phase 13: Security and Compliance
- **Objective:** Harden personal/private application security, enforce fail-closed zero-trust credentials, eliminate secret leakage risks, and establish operational kill switch and pre-flight gates.
- **Dependencies:** Phase 12.
- **Files/Modules:** `alphaforge/security/*`, `tests/unit/security/*`, `docs/PHASE_13_PERSONAL_SECURITY.md`, `.gitignore`.
- **Implementation Tasks:** Strict boolean parser (`parse_strict_bool`), paper-first execution mode defaults, dual-key live authorization (`TRADING_MODE=LIVE` and `LIVE_TRADING_ENABLED=True`), `SecretValue` wrapper with custom redaction, `CredentialStore` with paper/live isolation and dummy credential rejection, thread-safe `KillSwitch` with audit trail, `RedactionFormatter` for log and exception scrubbing, `SecurityStartupGate` pre-flight verification, `SecurityAuthorizer` and `SecureBroker` order decoration, executable security invariants.
- **Tests:** 38 dedicated security tests (SEC1–SEC15, ADV-SEC-1–7, REG-1–3, Invariants 1–7) covering paper-first defaults, fail-closed typos and malformed config, log scrubbing, credential isolation, kill-switch concurrency, mandatory dual gates, reconciliation gate blocking, and E2E secure order routing.
- **Acceptance Criteria:** Zero secrets present in codebase, logs, dumps, or serialized state; live trading mode strictly locked behind dual authorization keys; 100% test pass rate across 748 repository tests; zero diff against frozen Phase 0–12 baseline.
- **Exit Gate:** Clean security scan report, 38/38 security tests passing, 748/748 suite passing, formal documentation complete (`docs/PHASE_13_PERSONAL_SECURITY.md`); user sign-off.
- **Status:** IMPLEMENTED / VALIDATED (Freeze Ready).
- **Risks:** Accidental live execution or secret leakage; mitigated by fail-closed paper defaults, dual-key live gates, and automated log redaction.

---

### Phase 14: Observability
- **Objective:** Implement comprehensive, diagnostic operational observability and causal tracing across PAPER, SHADOW, and CONTROLLED LIVE execution without altering trading mathematics or domain semantics.
- **Dependencies:** Phase 13.
- **Files/Modules:** `alphaforge/observability/*`, `tests/unit/observability/*`, `docs/PHASE_14_OBSERVABILITY.md`.
- **Implementation Tasks:** Structured immutable `ObservabilityEvent` with automatic recursive secret redaction, deterministic SHA-256 identity with sequence ordinals and context propagation; thread-safe and async-safe `TraceContext` with `trace_span` causal lineage and monotonic counters; fail-safe `SafeObservabilityDispatcher` guaranteeing diagnostic isolation; bounded `InMemoryObservabilitySink` with deterministic ring buffer overflow behavior; append-only local `JsonlObservabilitySink`; monotonic in-memory `MetricsRegistry`; non-authoritative diagnostic `HealthAggregator` with priority rollup; structured logging with Phase 13 redaction; passive runtime adapters (`OrderFSMObservabilityAdapter`, `ReconciliationObservabilityAdapter`).
- **Tests:** 41 dedicated automated tests (OBS1–OBS22, ADV-OBS-1–12, ID-1–6, concurrent async child task identity, golden lifecycle trace, rejection trace, audit ledger separation) validating unbroken causal lineage, strict secret scrubbing, fail-safe crash isolation, security failure preservation, bounded memory overflow, and bit-exact semantic equivalence.
- **Acceptance Criteria:** Observability is strictly read-only diagnostics (`READ/RECORD/MEASURE/TRACE/DIAGNOSE`), never modifies domain semantics or trading decisions; diagnostic sink crashes never disrupt trading or mask security exceptions; zero secrets leaked; ZERO TRADING/SECURITY SEMANTIC CHANGE on frozen baseline (4 domain files contain passive observation hooks only: `data/normalization.py`, `strategy/engine.py`, `risk/engine.py`, `security/authorizer.py`); 100% test pass rate across all 789 repository tests.
- **Exit Gate:** 41/41 observability tests passing, 789/789 full suite passing, strict mypy and ruff passing, clean diff, formal documentation complete (`docs/PHASE_14_OBSERVABILITY.md`); user sign-off.
- **Status:** IMPLEMENTED / VALIDATED (Freeze Ready).
- **Risks:** Telemetry failure impacting execution; mitigated by fail-safe isolation layer guaranteeing zero trading interference.

---

### Phase 15: Deployment Environments
- **Objective:** Establish deterministic, fail-closed deployment runtime boundaries and directory isolation across DEV, TEST, PAPER, SHADOW, and LIVE environments for personal/private quantitative trading.
- **Dependencies:** Phase 14.
- **Files/Modules:** `alphaforge/deployment/*`, `tests/unit/deployment/*`, `docs/PHASE_15_DEPLOYMENT.md`.
- **Implementation Tasks:** `DeploymentEnvironment` string enum (PAPER default, fail-closed parsing); immutable `DeploymentConfig` with deterministic path derivation and traversal protection; `DeploymentBrokerGuard` enforcing execution safety (simulation orders to live broker impossible); `EnvironmentDirectoryManager` with `.alphaforge_env_marker` crossover protection; `DeploymentReadinessChecker` read-only pre-flight diagnostic probes; `DeploymentRuntime` with ordered startup validation and clean safe shutdown; `DeterministicBackupManager` producing bit-for-bit reproducible ZIP archives with canonical manifests; controlled LIVE recovery prohibiting generic restore into LIVE; `RollbackCoordinator` preventing cross-environment rollbacks; deterministic `DeploymentIdentity` combining git revision and configuration hash.
- **Tests:** 38 dedicated automated tests (ENV-1–ENV-20, ADV-1–ADV-17, SEM-EQ-1) verifying environment isolation, fail-closed parsing, broker guard invariants, startup ordering, safe shutdown, bit-exact backup determinism, rollback safety, secret scrubbing, and order semantic equivalence.
- **Acceptance Criteria:** PAPER strictly defaults; LIVE requires explicit dual authorization; simulation execution cannot route to live broker; backups are bit-for-bit deterministic; cross-environment restore into LIVE is strictly blocked; zero secrets in logs/dumps/payloads; zero modifications to frozen Phase 0–14 domain code; 100% test pass rate across all 827 repository tests.
- **Exit Gate:** 38/38 deployment tests passing, 827/827 full suite passing, 0 Ruff errors, 0 strict Mypy errors, clean diff against Phase 14 baseline, formal documentation complete (`docs/PHASE_15_DEPLOYMENT.md`); user sign-off.
- **Status:** IMPLEMENTED / VALIDATED (Freeze Ready).
- **Risks:** Cross-environment state crossover; mitigated by directory marker verification and execution guard rejecting live routing in non-live environments.

---

### Phase 16: Paper / Shadow Trading
- **Objective:** Implement a production-like Paper / Shadow Trading forward validation engine enforcing zero-live-orders safety, dynamic evolving risk state, authoritative strategy bracket exits, symmetric FSM exit order routing, deterministic clock control, execution simulation with Phase 6 cost/slippage models, conservative SL-first OHLC ambiguity resolution, strict anti-lookahead causality, and continuous 6-point multi-entity reconciliation.
- **Dependencies:** Phase 15.
- **Files/Modules:** `alphaforge/paper_shadow/*`, `tests/unit/paper_shadow/*`, `tests/integration/paper_shadow/*`, `tests/adversarial/paper_shadow/*`, `tests/property/test_paper_shadow_properties.py`, `docs/PHASE_16_PAPER_SHADOW.md`.
- **Implementation Tasks:** Deterministic enums and immutable Pydantic schemas (`PaperShadowConfig`, `MarketEvent`, `PaperTradeRecord`, `ShadowObservationRecord`, `ForwardRunReport`); `PaperMarketDataValidator` enforcing timestamp monotonicity, timeframe-scaled staleness thresholds, forming candle quarantine, and physical OHLC boundaries; `DeterministicFillSimulator` with Phase 6 friction, optional stop/target levels, and `SL_FIRST_CONSERVATIVE` same-bar resolution; `PaperPnLTracker` maintaining fixed-point `Decimal` accounting, dynamic `PortfolioRiskState` derivation, authoritative signal exit level propagation, FIFO exits, and drawdown tracking; `PaperShadowOrderRouter` managing 17-state `OrderStateMachine`, single-intent `IdempotencyRegistry`, deterministic timestamp routing, and `DeploymentBrokerGuard(PaperBroker)` routing with zero broker operations in SHADOW mode; `ShadowComparator` passive diagnostic observer; `PaperShadowReconciler` continuously checking 6 authoritative entities; `PaperShadowEngine` and `ForwardValidationRunner` testbench with JSON checkpoint crash recovery and Phase 13 `KillSwitch` / Phase 14 `SafeObservabilityDispatcher` integration.
- **Tests:** 69 dedicated automated tests (Unit, Integration, Adversarial PS-1..PS-38, Property) verifying zero live orders in paper/shadow, cross-environment blocking, fail-closed environment validation (PS-37), idempotency, order quantity conservation, state recovery, conservative SL-first fill simulation, adverse slippage, non-negative fees, anti-lookahead causal monotonicity, dynamic evolving risk state (PS-32), authoritative strategy exit levels (PS-33), deterministic replay identity (PS-34), authoritative exit order routing (PS-35), exit identity lineage (PS-36), authoritative contract metadata fidelity (PS-38), kill-switch halting, and ledger chain integrity.
- **Acceptance Criteria:** Real-money live broker order submission strictly impossible in PAPER and SHADOW modes; dynamic risk feedback loop; authoritative strategy-driven bracket exits; symmetric FSM routing on entry and exit; deterministic event clock control; conservative SL-first intra-bar ambiguity resolution; 100% fixed-point Decimal arithmetic; continuous reconciliation with auto-halt on discrepancy; zero look-ahead bias; zero modifications to frozen Phase 0–15 baseline; 100% test pass rate across 901 repository tests.
- **Exit Gate:** 69/69 Phase 16 tests passing, 901/901 full suite passing, 0 Ruff errors, 0 strict Mypy errors, clean diff against Phase 15 baseline, formal documentation complete (`docs/PHASE_16_PAPER_SHADOW.md`); user sign-off.
- **Status:** COMPLETED / FROZEN (Phase 16 Forensic Pass).
- **Risks:** Data leakage / lookahead bias or accidental live routing; mitigated by strict causal timestamp verification, forming-candle quarantine, and authoritative `DeploymentBrokerGuard` isolation.

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
