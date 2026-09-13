# ALPHAFORGE — PHASE 16: PAPER / SHADOW TRADING SPECIFICATION & FORENSIC AUDIT

**Project Name:** AlphaForge  
**Module:** `alphaforge/paper_shadow/`  
**Author:** Quantitative Systems Engineer, Senior Execution & Forensics Specialist  
**Status:** COMPLETED / FROZEN (Phase 16 Forensic Pass)  
**Phase 15 Frozen Baseline:** `81fba27684d848beecbd6d2d3e8f484fd090b0fe`  
**Phase 14 Frozen Baseline:** `33a5475f6d6da6b617d8a1d607030fd0415fb902`  

---

## 1. Phase 16 Objective & Scope

The objective of Phase 16 is to provide a production-grade **Paper / Shadow Trading validation layer** for AlphaForge. This system validates the frozen end-to-end trading pipeline against realistic market conditions with zero risk of submitting real-money orders.

Phase 16 operates in two distinct, strictly isolated modes:
1. **PAPER Mode (`PaperShadowMode.PAPER`)**: Simulates order execution, fill generation, position tracking, and dynamic account P&L using the Phase 6 cost and slippage models alongside the Phase 15 `PaperBroker`. Live broker routing is strictly blocked.
2. **SHADOW Mode (`PaperShadowMode.SHADOW`)**: Passively ingests real-time/replayed market feeds, evaluates frozen strategy signals and risk rules, and records hypothetical executions and shadow observations without submitting any orders to any broker.

---

## 2. Architectural Principles & Invariants

```
Market Data Ingestion (Causal Stream <= T)
  │
  ▼
PaperMarketDataValidator (Monotonicity, Staleness, Forming-Candle Quarantine, Geometry)
  │
  ▼
DeterministicStrategyEngine (Frozen Phase 1 — Closed Candle [1] vs Forming Candle [0])
  │
  ▼
RiskEngine (Frozen Phase 5 — Dynamic PortfolioRiskState from PaperPnLTracker)
  │
  ▼
PaperShadowOrderRouter (IdempotencyRegistry + 17-State OrderStateMachine)
  │
  ▼
DeploymentBrokerGuard (Frozen Phase 15 — Reject LIVE, Route PAPER to PaperBroker, SHADOW to None)
  │
  ▼
DeterministicFillSimulator (Phase 6 Cost/Slippage Models + SL-First Conservative Resolution)
  │
  ▼
PaperPnLTracker (Fixed-point Decimal, Signal Exit Bounds, Multi-Timeframe MTM, Realized P&L)
  │
  ▼
PaperShadowReconciler (Continuous 6-Entity Alignment + Auto-Halt on Discrepancy)
  │
  ▼
AuditLedger & ObservabilityHub (Phase 9 SHA-256 Hash Chain + Phase 14 Non-Blocking Telemetry)
```

### Core Invariants Enforced:
1. **Zero Real Broker Orders**: Under no circumstances can PAPER or SHADOW modes place orders with real live brokers. Enforced by `DeploymentBrokerGuard` and fail-closed environment validation (`DeploymentSafetyError`).
2. **Strict Causal Data Visibility**: Logic at timestamp $T$ has access only to market data with timestamps $\le T$. Future bars at $T+1$ cannot alter trading decisions, risk limits, fills, or metrics at $T$ (`PS-31`).
3. **Dynamic Evolving Risk State**: `RiskEngine` evaluates real evolving paper portfolio equity, cash, open notionals, and drawdowns derived continuously from `PaperPnLTracker.get_portfolio_risk_state()` (`PS-32`).
4. **Authoritative Strategy Exit Levels**: Bracket exits (stop-loss and profit-target) are strictly governed by the authoritative strategy signal's `stop_reference` and `target_reference` with zero hardcoded arbitrary formulas (`PS-33`).
5. **Symmetric FSM Exit Order Routing**: Exit orders follow the identical authoritative lifecycle as entry orders: `OrderRouter.create_and_route_order(role=OrderRole.EXIT)` $\to$ `IdempotencyRegistry` $\to$ `OrderStateMachine` $\to$ `DeploymentBrokerGuard` $\to$ `PaperBroker` (`PS-35`).
6. **Exit Identity Lineage Invariant**: Authoritative exit order ID created by `OrderRouter` is the single identity passed to fill simulation, FSM fill transitions, `PaperPnLTracker.record_exit_fill`, and `AuditLedger` causation records (`PS-36`).
7. **Fail-Closed Environment Validation**: `PaperShadowConfig` and `PaperShadowOrderRouter` strictly reject conflicting environment pairings (`PAPER + LIVE`, `SHADOW + LIVE`, `PAPER + SHADOW`, `SHADOW + PAPER`) at initialization (`PS-37`).
8. **Contract Metadata Fidelity & Lifecycle**: Validates contracts using Phase 3 `ContractMetadataProvider` and enforces contract tradability via `evaluate_contract_lifecycle` (`PS-38`).
9. **Conservative SL-First OHLC Resolution**: When a candle's price range satisfies both stop-loss and target-profit thresholds, execution resolves conservatively to the stop-loss first (`PS-18`).
10. **Deterministic Event Clock**: All order creation, fills, FSM state transitions, observation logs, reconciliations, and metrics are stamped with the deterministic market event exchange timestamp.
11. **Fixed-Point Arithmetic**: 100% of price, quantity, fee, slippage, equity, and P&L calculations utilize Python's `Decimal` type.
12. **Continuous Multi-Entity Reconciliation**: Reconciles (1) Execution Intents, (2) FSM Orders, (3) Broker Orders, (4) Broker Positions, (5) PnL Positions, and (6) Audit Ledger events every cycle.

---

## 3. Component Architecture

### `alphaforge/paper_shadow/models.py`
- Immutable Pydantic v2 schemas: `PaperShadowConfig`, `MarketEvent`, `PaperFill`, `ActivePositionState`, `PaperTradeRecord`, `ShadowObservationRecord`, `ForwardRunReport`.
- Enforces strict mode defaults (`PaperShadowMode.PAPER`), fail-closed environment validation, Decimal coercions, and UTC timezone requirements.

### `alphaforge/paper_shadow/market_data_validator.py`
- `PaperMarketDataValidator`: Validates streaming/replayed candles.
- Enforces exchange timestamp monotonicity, configurable staleness thresholds, forming candle quarantine (`is_closed=False` $\to$ `FORMING_BAR_LEAK`), zero/negative volume checks, and physical OHLC boundary verification ($High \ge \max(Open, Close)$ and $Low \le \min(Open, Close)$).

### `alphaforge/paper_shadow/fill_simulator.py`
- `DeterministicFillSimulator`: Pure deterministic fill calculation engine.
- Implements adverse slippage calculation via Phase 6 `calculate_slippage_bps`, non-negative execution fees via `calculate_total_cost`, volume participation limits, and `OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE` same-bar ambiguity resolution.
- Decouples `check_bracket_trigger` from `simulate_bracket_fill` to ensure authoritative order IDs are assigned to fills.

### `alphaforge/paper_shadow/pnl_tracker.py`
- `PaperPnLTracker`: Dynamic fixed-point portfolio ledger.
- Tracks active positions with signal stop/target levels, maintains mark-to-market valuations, computes realized and unrealized P&L, fees paid, and high-water mark drawdowns.
- Provides dynamic `get_portfolio_risk_state()` feeding evolving equity directly into `RiskEngine`.

### `alphaforge/paper_shadow/order_router.py`
- `PaperShadowOrderRouter`: Authoritative order orchestration adapter.
- Connects Phase 7 `OrderStateMachine`, Phase 8 `IdempotencyRegistry`, and Phase 15 `DeploymentBrokerGuard`.
- In `PAPER` mode, validates safety through `DeploymentBrokerGuard` and routes to `PaperBroker`.
- In `SHADOW` mode, validates FSM transitions and idempotency without calling broker APIs.
- Enforces fail-closed environment validation against mismatched configurations.

### `alphaforge/paper_shadow/shadow_comparator.py`
- `ShadowComparator`: Passive observer for shadow mode.
- Records hypothetical fills, slippage, costs, and divergence metrics alongside real/replayed market data.

### `alphaforge/paper_shadow/reconciler_adapter.py`
- `PaperShadowReconciler`: Continuous 6-entity verification engine.
- Flags mismatches across intents, FSM orders, broker orders, broker positions, tracker positions, and ledger records, halting new order entries on discrepancy.

### `alphaforge/paper_shadow/engine.py` & `forward_runner.py`
- `PaperShadowEngine`: Complete pipeline coordinator.
- Manages lifecycle (`INITIALIZING` $\to$ `RUNNING` $\to$ `PAUSED` $\to$ `STOPPED` $\to$ `TERMINATED` $\to$ `ERROR`), contract lifecycle checks, dynamic risk evaluation, symmetric entry/exit FSM routing, kill-switch integration, crash recovery from JSON checkpoints, and deterministic `ForwardRunReport` generation.

---

## 4. Forensic Remediation Summary

Phase 16 underwent comprehensive forensic audits and targeted remediations resolving key architectural requirements:

1. **Remediation 1 (Dynamic Risk State)**: Replaced static constants with `pnl_tracker.get_portfolio_risk_state()` dynamically calculating live equity, available capital, and open notionals from `initial_capital` and P&L history (`PS-32`).
2. **Remediation 2 (Authoritative Exit Levels)**: Eliminated hardcoded synthetic percentages. `ActivePositionState` preserves signal `stop_reference` and `target_reference`, evaluated directly during bracket simulation (`PS-33`).
3. **Remediation 3 (Deterministic Clock Control)**: Replaced wall-clock `datetime.now()` calls with deterministic `candle.exchange_timestamp` across orders, FSM transitions, fills, observations, reconciliations, and reports (`PS-34`).
4. **Remediation 4 (Symmetric Exit Order Routing)**: Exit orders strictly follow the authoritative path (`OrderRouter.create_and_route_order(role=OrderRole.EXIT)` $\to$ `IdempotencyRegistry` $\to$ `OrderStateMachine` $\to$ `DeploymentBrokerGuard` $\to$ `PaperBroker`) (`PS-35`).
5. **Remediation 5 (Exit Identity Lineage)**: Decoupled bracket trigger detection from fill simulation. Authoritative exit order is created first, and its exact `order_id` is propagated across fill simulation, FSM fill transition, P&L record, and ledger audit event (`PS-36`).
6. **Remediation 6 (Fail-Closed Environment Validation)**: Replaced silent environment rewriting with strict fail-closed `DeploymentSafetyError` validation in `PaperShadowConfig` and `PaperShadowOrderRouter` (`PS-37`).
7. **Remediation 7 (Contract Metadata Fidelity & Lifecycle)**: Integrated Phase 3 `ContractMetadataProvider` into `PaperShadowEngine`, verifying contract lot sizes, multipliers, and active lifecycle constraints (`PS-38`).

---

## 5. Comprehensive Test Matrix & Validation Results

The Phase 16 test suite contains **69 dedicated automated tests** covering unit, integration, adversarial safety, and property-based validation:

### 1. Adversarial Safety & Invariant Tests (`tests/adversarial/paper_shadow/test_paper_shadow_adversarial.py`)

| Test ID | Description | Result |
| :--- | :--- | :--- |
| **PS-1** | PAPER environment pairing with live broker is rejected fail-closed | **PASS** |
| **PS-2** | SHADOW mode never invokes broker methods or submits orders | **PASS** |
| **PS-3** | Unauthorized LIVE execution attempt in paper harness fails closed | **PASS** |
| **PS-4** | Cross-environment guard validation blocks order routing to live broker | **PASS** |
| **PS-5** | Idempotency registry prevents duplicate execution intent registration | **PASS** |
| **PS-6** | Position quantity cannot exceed authoritative executed quantity | **PASS** |
| **PS-7** | Partial fill accounting updates remaining quantity and VWAP correctly | **PASS** |
| **PS-8** | Full fill accounting closes position completely | **PASS** |
| **PS-9** | Position quantity conservation holds across multiple partial fills | **PASS** |
| **PS-10** | Cold-boot reconciler reconstructs order, fill, and position from storage | **PASS** |
| **PS-11** | Reconciler identifies missing local order from broker state | **PASS** |
| **PS-12** | Reconciler flags state mismatch when local and broker states diverge | **PASS** |
| **PS-13** | Reconciler detects discrepancy when broker position diverges from local state | **PASS** |
| **PS-14** | Reconciler flags mismatches rather than silently rewriting local state | **PASS** |
| **PS-15** | Identical market inputs produce bit-for-bit identical fills | **PASS** |
| **PS-16** | Slippage model applies strictly adverse price impact for buyer and seller | **PASS** |
| **PS-17** | Transaction fees are strictly non-negative and deducted from net P&L | **PASS** |
| **PS-18** | Ambiguous intra-bar SL/TP scenario resolves to stop-loss first (conservative) | **PASS** |
| **PS-19** | Zero-volume market candle prevents fill execution | **PASS** |
| **PS-20** | Stale market data timestamp triggers rejection in validator | **PASS** |
| **PS-21** | Non-monotonic candle sequence is detected and quarantined | **PASS** |
| **PS-22** | Invalid OHLC geometry ($High < Low$) is rejected | **PASS** |
| **PS-23** | Corrupted payload does not crash engine; engine fails closed safely | **PASS** |
| **PS-24** | Forming candle (`is_closed=False`) is quarantined from decision path | **PASS** |
| **PS-25** | Shutdown generates report certifying zero live orders were submitted | **PASS** |
| **PS-26** | Invalid configuration fails closed at startup with zero orders | **PASS** |
| **PS-27** | Kill switch blocks new orders even in simulation | **PASS** |
| **PS-28** | Frozen `RiskEngine` limits remain strictly enforced in Paper mode | **PASS** |
| **PS-29** | 17-state FSM enforces legal state transitions only | **PASS** |
| **PS-30** | Audit ledger cryptographic hash chain verifies integrity | **PASS** |
| **PS-31** | Strict causal data visibility: Future data at $T+1$ cannot alter metrics at $T$ | **PASS** |
| **PS-32** | Dynamic Evolving Risk & Account State: Evolving P&L dynamically updates risk state | **PASS** |
| **PS-33** | Authoritative Strategy Exit Levels: Bracket exits strictly evaluate signal stop/target | **PASS** |
| **PS-34** | Deterministic Replay & Bit-for-Bit Identity: Two runs produce identical results | **PASS** |
| **PS-35** | Exit Orders Use Authoritative Routing & FSM: Exit path enforces full FSM & guard | **PASS** |
| **PS-36** | Exit Order, Fill, P&L, and Ledger Identity Lineage: Unified authoritative identity | **PASS** |
| **PS-37** | Fail-Closed Environment Validation: Mismatched configs reject with DeploymentSafetyError | **PASS** |
| **PS-38** | Authoritative Contract Metadata Fidelity: Metadata and lifecycle status enforced | **PASS** |

### 2. Unit, Integration & Property Test Breakdown

- `tests/unit/paper_shadow/test_models.py` (6/6 tests): Model immutability, validation constraints, hash determinism.
- `tests/unit/paper_shadow/test_market_data_validator.py` (6/6 tests): Monotonicity, stale detection, forming bar quarantine, OHLC invariants.
- `tests/unit/paper_shadow/test_fill_simulator.py` (5/5 tests): Fills, adverse slippage, fees, SL-first resolution, volume capping.
- `tests/unit/paper_shadow/test_pnl_tracker.py` (3/3 tests): Fixed-point Decimal P&L, FIFO partial exits, drawdown tracking.
- `tests/unit/paper_shadow/test_order_router.py` (3/3 tests): Idempotent routing, SHADOW mode isolation, duplicate prevention.
- `tests/unit/paper_shadow/test_shadow_comparator.py` (1/1 tests): Passive observation recording and hypothetical fill evaluation.
- `tests/unit/paper_shadow/test_reconciler_adapter.py` (2/2 tests): Clean alignment and phantom position discrepancy detection.
- `tests/integration/paper_shadow/test_engine_lifecycle.py` (3/3 tests): Startup, candle processing, state checkpoint recovery, and clean shutdown.
- `tests/integration/paper_shadow/test_forward_runner.py` (2/2 tests): Batch forward validation runs across PAPER and SHADOW modes.
- `tests/property/test_paper_shadow_properties.py` (2/2 tests): Hypothesis property-based testing of P&L conservation and monotonic sequence validation.

---

## 6. Full Repository Regression Results

```text
============================= test session starts =============================
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\AlphaForge
configfile: pyproject.toml
plugins: hypothesis-6.168.0
collected 901 items

........................................................................ [  7%]
........................................................................ [ 15%]
........................................................................ [ 23%]
........................................................................ [ 31%]
........................................................................ [ 39%]
........................................................................ [ 47%]
........................................................................ [ 55%]
........................................................................ [ 63%]
........................................................................ [ 71%]
........................................................................ [ 79%]
........................................................................ [ 87%]
........................................................................ [ 95%]
.....................................                                    [100%]

============================= 901 passed in 20.42s ==============================
```

### Static Analysis & Type Checking
- **Ruff Linter**: `ruff check alphaforge tests` $\to$ **All checks passed! (0 errors)**
- **Ruff Formatter**: `ruff format --check alphaforge tests` $\to$ **225 files already formatted (0 discrepancies)**
- **Mypy Strict Type Check**: `mypy alphaforge` $\to$ **Success: no issues found in 117 source files**

---

## 7. Frozen Baseline Diff Audit

Verification against the frozen Phase 15 commit `81fba27684d848beecbd6d2d3e8f484fd090b0fe`:

```powershell
git diff 81fba27684d848beecbd6d2d3e8f484fd090b0fe --name-status
```
**Output**: All production code changes strictly confined to `alphaforge/paper_shadow/*`. Zero modifications to Phase 0–15 frozen packages (`strategy`, `risk`, `execution`, `ledger`, `security`, `observability`, `deployment`, `contract`, `broker`).

---

## 8. Explicit Disclaimer & Forensic Boundaries

> [!IMPORTANT]
> **Zero Live Orders Certification**:
> **No real broker orders were submitted.** Under no circumstances were live broker connections initiated or real-money orders placed during any phase of implementation, testing, or forward validation. All tests ran against offline, deterministic synthetic feeds or recorded fixtures.

> [!WARNING]
> **Non-Certification of Live Profitability**:
> **Phase 16 freeze does not constitute profitability certification or live-trading authorization.** Freezing Phase 16 certifies software implementation correctness, architectural safety, state conservation, and deterministic causal execution. Live capital deployment remains strictly gated behind Phase 17 dual-authorization procedures.

---

## 9. 31-Item Forensic Self-Check & Acceptance Verification

1. **Was any live broker order submitted during tests or forward runs?**
   *No. No real broker orders were submitted.*
2. **Does PAPER mode default when configuration is unspecified?**
   *Yes. `PaperShadowMode.PAPER` is the strict default.*
3. **Does SHADOW mode guarantee zero order submissions?**
   *Yes. In SHADOW mode, the router returns `None` and never invokes broker APIs.*
4. **Is look-ahead bias strictly eliminated?**
   *Yes. Causal visibility is enforced; future candle at $T+1$ cannot alter metrics at $T$ (verified by `PS-31`).*
5. **Are intra-bar SL/TP ambiguities resolved conservatively?**
   *Yes. `OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE` prioritizes stop-loss execution (verified by `PS-18`).*
6. **Are transaction costs and slippages modeled accurately?**
   *Yes. Uses Phase 6 `CostConfig` and adverse effective price calculators.*
7. **Is all financial arithmetic in Decimal?**
   *Yes. 100% of prices, fees, P&L, and exposures use fixed-point `Decimal`.*
8. **Does the reconciler check all 6 authoritative entities?**
   *Yes. Intents, FSM Orders, Broker Orders, Broker Positions, PnL Positions, and Ledger.*
9. **Does reconciliation mismatch halt new order entry?**
   *Yes. `new_entries_allowed` is set to `False` on any mismatch.*
10. **Are forming candles quarantined?**
    *Yes. `is_closed=False` raises `MarketDataAnomalyType.FORMING_BAR_LEAK` (verified by `PS-24`).*
11. **Are timestamps verified for UTC timezone awareness?**
    *Yes. Pydantic validators strictly require timezone-aware UTC datetime objects.*
12. **Are sequence numbers monotonically validated?**
    *Yes. Non-monotonic or duplicate sequences are quarantined.*
13. **Is the 17-state Order FSM authoritative?**
    *Yes. Illegal state transitions fail with `IllegalStateTransitionError`.*
14. **Is single-intent idempotency enforced?**
    *Yes. `IdempotencyRegistry` prevents duplicate client order IDs.*
15. **Are order quantities conserved across partial fills?**
    *Yes. `local_quantity == filled_quantity + remaining_quantity` is verified.*
16. **Is position quantity conserved across entries and exits?**
    *Yes. Local tracker maintains strict quantity conservation.*
17. **Is the KillSwitch integrated and respected?**
    *Yes. When engaged, new simulated and live orders are immediately blocked (verified by `PS-27`).*
18. **Is state checkpointing deterministic?**
    *Yes. JSON checkpoint persistence restores exact engine state.*
19. **Are all test feeds deterministic and network-free?**
    *Yes. Tests use synthetic in-memory fixtures and recorded feeds.*
20. **Did any test require internet access or live credentials?**
    *No. 100% offline deterministic execution.*
21. **Is risk state dynamically updated with evolving portfolio P&L?**
    *Yes. `pnl_tracker.get_portfolio_risk_state()` passes live equity to `RiskEngine` (verified by `PS-32`).*
22. **Are bracket exits driven strictly by authoritative strategy signals?**
    *Yes. Evaluates `stop_reference` / `target_reference` with zero synthetic formulas (verified by `PS-33`).*
23. **Are replay executions bit-for-bit reproducible?**
    *Yes. Independent runs produce identical metrics and reports (verified by `PS-34`).*
24. **Are exit orders routed through full FSM and broker guard?**
    *Yes. Symmetrically validated through `DeploymentBrokerGuard` (verified by `PS-35`).*
25. **Is exit order identity lineage preserved across FSM, fill, P&L, and ledger?**
    *Yes. Authoritative exit order ID is unified across all subsystems (verified by `PS-36`).*
26. **Do mismatched deployment environments fail closed?**
    *Yes. `DeploymentSafetyError` is raised immediately upon configuration or router mismatch (verified by `PS-37`).*
27. **Are contract metadata and lifecycle constraints enforced?**
    *Yes. Lot sizes, multipliers, and tradability evaluated via `ContractMetadataProvider` (verified by `PS-38`).*
28. **Are all 69 Phase 16 tests passing?**
    *Yes. 69/69 tests pass.*
29. **Are all 901 repository tests passing?**
    *Yes. 901/901 full regression pass.*
30. **Is Ruff and Mypy 100% clean?**
    *Yes. 0 linter errors, 0 format issues, 0 type errors across 117 files.*
31. **Were any Phase 0–15 files modified?**
    *No. Zero diff on frozen baseline (`81fba27684d848beecbd6d2d3e8f484fd090b0fe`).*

---

## 10. Final Phase Verdict

```text
===============================================================================
                     PHASE 16 — FORENSIC PASS / FREEZE READY
===============================================================================
  - Component: Paper / Shadow Trading Forward Validation Engine
  - Package: alphaforge/paper_shadow/
  - Unit / Integration / Adversarial / Property Tests: 69 / 69 PASSED (100%)
  - Full Repository Regression Tests: 901 / 901 PASSED (100%)
  - Static Code Analysis: Ruff Clean (0 errors), Ruff Format Clean (225 files)
  - Type Safety: Mypy Strict Clean (0 errors across 117 files)
  - Frozen Baseline Integrity: ZERO diff on Phase 0–15 code
===============================================================================
```
