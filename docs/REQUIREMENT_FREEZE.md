# ALPHAFORGE — PHASE 0: REQUIREMENT FREEZE SPECIFICATION

**Project Name:** AlphaForge  
**Document Type:** Formal Scope & Requirement Contract  
**Phase:** Phase 0 — Requirement Freeze  
**Author:** Principal Software Architect, Senior Quantitative Systems Engineer, Reliability Engineer, Security Engineer, and Code Auditor  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan (Master Specification)

---

## 1. Master Constitution Authority & Change Control Contract

The document titled **"Development & Production-Ready Final Plan"** is the **MASTER SPECIFICATION** and absolute source of truth for AlphaForge.

1. **No Silent Changes:** No requirement may be removed, simplified, modified, or bypassed without explicit user authorization.
2. **Defect & Contradiction Protocol:** If any requirement is discovered to be technically incorrect, contradictory, incomplete, or operationally unsafe:
   - It will NOT be silently altered.
   - It will be reported explicitly with Severity, Problem Statement, Risk Assessment, and the Minimal Proposed Change.
   - It will remain in its original state until explicit, documented approval is granted.
3. **Precedence:** The Master Specification takes absolute precedence over all engineer assumptions, auditor proposals, and design defaults.

---

## 2. Core Engineering Principles (Immutable Baseline)

AlphaForge must strictly satisfy the 11 Core Engineering Principles across all phases:

| Principle | Formal Definition | Enforcement Level |
| :--- | :--- | :--- |
| **Determinism** | Given identical input market data, identical versioned configuration, and identical dependencies, the system must produce the bit-exact same decision and state transition. | Mathematical Invariant |
| **Data Safety** | Invalid, stale, missing, duplicate, inconsistent, or desynchronized market data must never silently reach the strategy or execution engines. | Fail Closed |
| **Risk Independence** | Risk controls must be completely independent from strategy logic with unilateral veto authority. Strategy cannot alter or bypass risk limits. | Architectural Boundary |
| **Execution Safety** | A strategy must never directly submit broker orders or interact with broker network protocols. Execution is mediated by the Order State Machine. | Architectural Boundary |
| **Idempotency** | The same logical signal, order intent, or network retry must never create unintended duplicate orders at the broker or in local state. | Mathematical Invariant |
| **Reconciliation** | Broker state is the external ground truth that must be continuously reconciled with internal state. Mismatches block new entries. | Operational Invariant |
| **Crash Recovery** | The system must safely recover without state corruption or blind re-execution after process, network, database, or broker failures. | Operational Invariant |
| **Auditability** | Every decision (data acceptance, feature computation, signal, risk veto, order transition, fill, reconciliation) must be recorded with tamper evidence. | Cryptographic Ledger |
| **Replayability** | Historical market sessions and live incidents must be 100% reproducible through deterministic replay without dependencies on wall-clock time. | Test Invariant |
| **Controlled Promotion** | Promotion must strictly follow: Research $\to$ Development $\to$ Paper $\to$ Shadow $\to$ Tiny Live $\to$ Production. No uncontrolled transition is permitted. | Operational Invariant |
| **Fail Closed** | Whenever the system cannot establish with certainty that trading is safe, it must immediately reject the trade and lock entries rather than guess. | Core Safety Philosophy |

---

## 3. Mandatory Closed-Candle Invariant (Rule 8)

The candle indexing convention is frozen across the entire AlphaForge architecture:

```text
[0] = Current Forming Candle (Still Mutating / Live Tick Accumulator)
[1] = Latest Fully Closed Candle (Completed Bar)
[2] = Previous Fully Closed Candle (Completed Bar)
...
[N] = Historical Fully Closed Candles
```

- **Enforcement:** Signal generation MUST use only candle `[1]` and older data.
- **Strict Prohibition:** Under no circumstances may any strategy component, indicator calculation, or entry rule read `[0]` for entry decisions.
- **Verification:** Covered by mandatory unit tests, property-based fuzz tests (`hypothesis`), golden fixture replays, and failure injection.

---

## 4. V1 Scope Freeze

### 4.1 In-Scope Capabilities (Mandatory V1 Baseline)
The following capabilities are formally frozen as the V1 delivery scope:
- Single deterministic trading strategy.
- Multi-timeframe signal generation.
- Underlying cash index and front-month futures dual confirmation.
- Single entry per setup (100% of calculated lot size).
- Single stop-loss placed immediately upon fill.
- Single profit target.
- Lot-size-aware position sizing math.
- Futures contract lifecycle management (expiry calendar, rollover blackout).
- Index-Futures basis monitoring (basis points, basis %, basis z-score, timestamp desync).
- Realistic transaction cost model (STT, stamp duty, exchange turnover, GST, brokerage) and slippage model.
- Formal independent risk engine.
- 17-state deterministic order state machine.
- Idempotent order submission with deterministic client order IDs.
- Broker reconciliation engine (periodic sync and startup recovery).
- 9-step crash recovery protocol.
- Audit ledger (append-only, tamper-evident).
- Deterministic event replay engine.
- Backtest engine with out-of-sample and walk-forward validation.
- Paper trading and shadow broker execution modes.
- Tiny-capital live deployment gate (single lot under supervision).
- Multi-channel kill switch and operational alerting.
- Incident runbooks, semantic versioning, and change control.

### 4.2 Excluded Capabilities (Strictly Excluded from V1)
Unless formally modified through an approved change request, the following items are strictly prohibited in V1:
- **NO** AI / Deep Learning signal generation.
- **NO** ML scoring, random forests, or dynamic weighting.
- **NO** News sentiment or social media scraping.
- **NO** Averaging down or Martingale position sizing under adverse excursion.
- **NO** Automatic live scaling of position size.
- **NO** Complex multi-stage exits, laddering, or discretionary partial profit takes.
- **NO** Uncontrolled strategy switching or dynamic strategy rotation.
- **NO** Multiple correlated strategies without a formal portfolio-level risk engine.

---

## 5. Authoritative Phase Numbering & Dependencies

The development sequence is permanently locked to the 18 phases defined in the Master Specification:

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

## 6. Safety & Operational Constraints During Phase 0

- **Trading State:** `LIVE_TRADING = FALSE`
- **Network Permissions:** Zero live broker API connectivity permitted.
- **Credential Hygiene:** No broker credentials or secrets stored or requested.
- **Code Execution:** Zero strategy, risk, or execution code implemented during Phase 0.
