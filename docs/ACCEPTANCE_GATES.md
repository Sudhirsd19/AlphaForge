# ALPHAFORGE — PHASE ACCEPTANCE GATES & PROMOTION CRITERIA

**Project Name:** AlphaForge  
**Document Type:** Formal Phase Exit Gates & Verification Standard  
**Phase:** Phase 0 — Requirement Freeze  
**Author:** Principal Software Architect, Senior Quantitative Systems Engineer, Reliability Engineer  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Baseline Specification:** AlphaForge Master Constitution (Authoritative Phases 0 to 17)

---

## 1. Governance & Promotion Rules

AlphaForge enforces strict sequential promotion gates:
1. **Zero Skipping:** No phase may be skipped, bypassed, or merged.
2. **Deterministic Exit Gates:** Each phase has explicit, measurable pass/fail criteria.
3. **No Automatic Transition:** Passing an exit gate does NOT automatically begin the next phase. Implementation must STOP at every gate and await explicit user authorization.

---

## 2. Comprehensive Phase Gate Matrix (Phases 0 to 17)

```text
[ Phase 0: Requirement Freeze ]
              |
              v (Explicit User Approval: "PROCEED TO PHASE 1")
[ Phase 1: Deterministic Strategy Specification ]
              |
              v (Explicit User Approval)
[ Phase 2: Data Governance & Market Data Layer ]
              |
              v (Explicit User Approval)
[ Phase 3: Futures & Contract Lifecycle Engine ]
              |
              v (Explicit User Approval)
[ Phase 4: Index-Futures Basis Engine ]
              |
              v (Explicit User Approval)
[ Phase 5: Risk Engine ]
              |
              v (Explicit User Approval)
[ Phase 6: Cost and Slippage Model ]
              |
              v (Explicit User Approval)
[ Phase 7: Order State Machine ]
              |
              v (Explicit User Approval)
[ Phase 8: Idempotency & Crash Recovery ]
              |
              v (Explicit User Approval)
[ Phase 9: Audit Ledger ]
              |
              v (Explicit User Approval)
[ Phase 10: Backtest and Quant Validation ]
              |
              v (Explicit User Approval)
[ Phase 11: Replay Engine ]
              |
              v (Explicit User Approval)
[ Phase 12: Failure-Injection Testing ]
              |
              v (Explicit User Approval)
[ Phase 13: Security and Compliance ]
              |
              v (Explicit User Approval)
[ Phase 14: Observability ]
              |
              v (Explicit User Approval)
[ Phase 15: Deployment Environments ]
              |
              v (Explicit User Approval)
[ Phase 16: Paper / Shadow ]
              |
              v (Explicit User Approval)
[ Phase 17: Controlled Live ]
```

---

### Phase 0 Gate: Requirement Freeze
- **Prerequisites:** Full forensic audit of greenfield workspace; complete Master Specification review.
- **Verification Requirements:**
  - `REQUIREMENT_FREEZE.md` verified and aligned with Master Plan.
  - `FORMAL_REQUIREMENT_MATRIX.md` maps 100% of functional and safety requirements.
  - `OPEN_DESIGN_DECISIONS.md` separates mandatory requirements from proposed design options.
  - `ACCEPTANCE_GATES.md` defines verifiable exit criteria for all 18 phases.
  - Supporting infrastructure (Git repo, `pyproject.toml`, Ruff, mypy, pytest) operational.
- **Pass/Fail Threshold:** 0 unmapped requirements; 0 silent design assumptions.
- **Exit Gate Sign-off:** Explicit user command: `"PROCEED TO PHASE 1"`.

---

### Phase 1 Gate: Deterministic Strategy Specification
- **Prerequisites:** Phase 0 Exit Gate signed off.
- **Verification Requirements:**
  - Mathematical specification of single deterministic strategy.
  - Closed-candle isolation mechanism quarantining `[0]` forming candle.
  - Pure indicator functions taking explicit arrays without internal state.
  - Golden fixtures containing verified input/output candle sequences.
- **Pass/Fail Threshold:**
  - Property test: 1,000 randomized mutations to candle `[0]` yield 0 changes in signal output.
  - 100% passing golden fixture tests.
- **Exit Gate Sign-off:** Strategy logic verified purely deterministic and immune to lookahead bias.

---

### Phase 2 Gate: Data Governance and Market Data Layer
- **Prerequisites:** Phase 1 Exit Gate signed off.
- **Verification Requirements:**
  - Ingestion schema validation for all 15 canonical fields.
  - OHLC boundary assertions: $high \ge \max(open, close, low)$, $low \le \min(open, close, high)$.
  - Strict monotonic timestamp validation and duplicate handling.
  - Sequence gap detection with automated backfill orchestration.
- **Pass/Fail Threshold:** 100% detection and fail-closed rejection of corrupted/skipped candles.
- **Exit Gate Sign-off:** Automated tests verify zero invalid data can reach downstream components.

---

### Phase 3 Gate: Futures and Contract Lifecycle Engine
- **Prerequisites:** Phase 2 Exit Gate signed off.
- **Verification Requirements:**
  - Instrument master catalog with lot size and tick size definitions.
  - Front-month contract auto-selection algorithm.
  - Expiry calendar tracking and rollover blackout window enforcement.
- **Pass/Fail Threshold:** Deterministic contract resolution verified across 24 historical monthly rollovers.
- **Exit Gate Sign-off:** Contract selection is 100% reproducible for any historical or live timestamp.

---

### Phase 4 Gate: Index-Futures Basis Engine
- **Prerequisites:** Phase 3 Exit Gate signed off.
- **Verification Requirements:**
  - Dual stream synchronizer for Cash Index Spot and Futures.
  - Basis points, basis %, and rolling basis z-score calculations.
  - Feed timestamp desynchronization gate ($|\Delta ts| > \text{threshold}$).
  - Directional confirmation filter.
- **Pass/Fail Threshold:** Signals rejected with explicit codes on simulated basis spike or feed lag.
- **Exit Gate Sign-off:** Basis engine reliably blocks unconfirmed divergence trades.

---

### Phase 5 Gate: Risk Engine
- **Prerequisites:** Phase 4 Exit Gate signed off.
- **Verification Requirements:**
  - Independent risk gateway holding unilateral veto over strategy signals.
  - Discrete lot-size calculator: $\lfloor \frac{\text{Capital} \times r}{\Delta P \times \text{LotSize}} \rfloor$.
  - Rejection when $\text{allowed\_lots} < 1$ (`REJECT_RISK_BELOW_MINIMUM_LOT`).
  - Session-boundary daily loss hard circuit breaker.
  - Invariant assertion preventing averaging down or position size expansion after a loss.
- **Pass/Fail Threshold:** 100% rejection of risk-violating orders; zero rounding up to 1 lot permitted.
- **Exit Gate Sign-off:** All risk invariants verified by property-based and boundary unit tests.

---

### Phase 6 Gate: Cost and Slippage Model
- **Prerequisites:** Phase 5 Exit Gate signed off.
- **Verification Requirements:**
  - Exchange tariff engine calculating STT, Stamp Duty, Turnover Fees, GST, and Brokerage.
  - Volatility-dependent bid-ask spread slippage simulation.
- **Pass/Fail Threshold:** Calculated transaction costs match actual broker contract notes within $\pm 0.01\%$.
- **Exit Gate Sign-off:** Frictions accurately deducted in every simulated backtest fill.

---

### Phase 7 Gate: Order State Machine
- **Prerequisites:** Phase 6 Exit Gate signed off.
- **Verification Requirements:**
  - Implementation of formal 17-state finite state machine.
  - Strict transition guards throwing exceptions on illegal state paths.
  - Emergency Unprotected Position Watchdog (missing SL timeout triggers market exit).
- **Pass/Fail Threshold:**
  - Zero illegal transitions permitted across full $17 \times 17$ transition matrix test.
  - Emergency watchdog triggers exit and halts entries within target window upon simulated SL drop.
- **Exit Gate Sign-off:** Order state machine formally verified for zero unmonitored states.

---

### Phase 8 Gate: Idempotency and Crash Recovery
- **Prerequisites:** Phase 7 Exit Gate signed off.
- **Verification Requirements:**
  - Deterministic client order ID generation via cryptographic hashing.
  - High-fidelity simulated paper broker adapter.
  - 9-step cold boot crash recovery reconciler.
- **Pass/Fail Threshold:**
  - Zero duplicate orders submitted under duplicate webhook or retry conditions.
  - Process killed mid-execution (`SIGKILL`) recovers exact state and active stops upon restart.
- **Exit Gate Sign-off:** Complete crash resilience verified in automated integration tests.

---

### Phase 9 Gate: Audit Ledger
- **Prerequisites:** Phase 8 Exit Gate signed off.
- **Verification Requirements:**
  - Append-only event store recording all data, signals, risk vetoes, orders, and broker receipts.
  - Cryptographic hash chaining (`previous_event_hash` $\to$ `event_hash`).
- **Pass/Fail Threshold:** Mutating any historical ledger record causes cryptographic verification failure.
- **Exit Gate Sign-off:** 100% of trading decisions can be forensically reconstructed from ledger alone.

---

### Phase 10 Gate: Backtest and Quant Validation
- **Prerequisites:** Phase 9 Exit Gate signed off.
- **Verification Requirements:**
  - Event-driven backtesting engine running historical tick/minute data.
  - In-Sample (60%), Out-of-Sample (20%), and Walk-Forward Analysis (20%).
  - Monte Carlo 1,000-run trade sequence resampling.
  - Cost and slippage stress testing at $2\times$ and $3\times$ standard fees.
- **Pass/Fail Threshold:**
  - Strategy meets formal criteria: Expectancy $> 0.20$ R, Profit Factor $\ge 1.50$, Sample Size $\ge 150$.
  - Strategy officially classified as `VALIDATED` (or flagged `INCONCLUSIVE`/`REJECTED`).
- **Exit Gate Sign-off:** Formal quant validation report approved.

---

### Phase 11 Gate: Replay Engine
- **Prerequisites:** Phase 10 Exit Gate signed off.
- **Verification Requirements:**
  - Offline event replay runner feeding recorded market data through live pipeline.
  - Virtual clock provider decoupling execution from system wall clock.
- **Pass/Fail Threshold:** Replay of 5 distinct recorded trading days produces bit-exact identical signals, order transitions, and ledger hashes.
- **Exit Gate Sign-off:** Deterministic replay capability verified.

---

### Phase 12 Gate: Failure-Injection Testing
- **Prerequisites:** Phase 11 Exit Gate signed off.
- **Verification Requirements:**
  - Automated chaos test suite simulating all 17 failure modes from Master Specification Section 20.
- **Pass/Fail Threshold:** 17 out of 17 chaos scenarios pass with verified fail-closed behavior and zero unmonitored risk.
- **Exit Gate Sign-off:** Failure injection test report signed off.

---

### Phase 13 Gate: Security and Compliance
- **Prerequisites:** Phase 12 Exit Gate signed off.
- **Verification Requirements:**
  - Secret scanning confirms zero committed credentials or tokens.
  - Environment-based secret injection with strict schema validation.
  - Paper and live credentials physically isolated.
  - Pre-commit secret scanning hooks verified.
- **Pass/Fail Threshold:** Zero high/critical security findings; zero credential leaks.
- **Exit Gate Sign-off:** CISO security sign-off report approved.

---

### Phase 14 Gate: Observability
- **Prerequisites:** Phase 13 Exit Gate signed off.
- **Verification Requirements:**
  - Real-time telemetry: data feed latency, order-to-ack latency, heartbeat monitor.
  - Multi-channel alerting (Telegram bot / Webhook + local emergency sound).
  - Manual and automated emergency Kill Switch CLI.
- **Pass/Fail Threshold:** P0 emergency alert received within 2 seconds; Kill Switch freezes system immediately.
- **Exit Gate Sign-off:** Observability and emergency halt verified end-to-end.

---

### Phase 15 Gate: Deployment Environments
- **Prerequisites:** Phase 14 Exit Gate signed off.
- **Verification Requirements:**
  - Environment segregation verified across Research, Paper, Shadow, Tiny Live, Production.
  - Hard runtime guard: `LIVE_TRADING = FALSE` by default.
  - NTP time synchronization daemon operational with $< 50\text{ms}$ drift.
- **Pass/Fail Threshold:** Attempted live order placement fails with hard assertion when not in authorized live mode.
- **Exit Gate Sign-off:** Staging runtime verified ready for real-time market data ingestion.

---

### Phase 16 Gate: Paper / Shadow Execution
- **Prerequisites:** Phase 15 Exit Gate signed off.
- **Verification Requirements:**
  - Live market data feed connected to simulated matching engine during real exchange hours.
  - Real-time order state machine, risk gateway, basis engine, and ledger active in memory.
- **Pass/Fail Threshold:** Minimum 15 consecutive trading days of continuous zero-crash operation with zero state machine inconsistencies.
- **Exit Gate Sign-off:** Shadow execution report confirming fill dynamics and latency match theoretical models.

---

### Phase 17 Gate: Controlled Live Deployment
- **Prerequisites:** Phase 16 Exit Gate signed off; formal user authorization.
- **Verification Requirements:**
  - Live broker API credentials injected with Order-Only permissions (Withdrawals disabled).
  - Position size hardcoded to single lot ($1\text{ lot}$ max).
  - Real-time supervision desk and manual kill switch active.
- **Pass/Fail Threshold:** 100% order acknowledgment, confirmed exchange resting stop-loss orders, and bit-exact reconciliation with broker console.
- **Exit Gate Sign-off:** Formal Production Readiness Review completed.
