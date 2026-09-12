# ALPHAFORGE — PHASE 1 IMPLEMENTATION PLAN

**Project Name:** AlphaForge  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Document Type:** Engineering Implementation Plan  
**Author:** Principal Quantitative Systems Engineer, Principal Software Architect  
**Date:** 2026-09-12  
**Status:** IN PROGRESS  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan (Master Specification) & Phase 0 Requirement Freeze

---

## 1. Phase 1 Objectives

The sole objective of Phase 1 is:
> Converting the trading strategy into a deterministic, formal, machine-testable strategy specification and implementing the corresponding pure strategy-core logic.

The strategy engine must operate as a pure, side-effect-free mathematical function:
$$\text{StrategyInput} + \text{StrategyConfig} + \text{Context} \longrightarrow \text{StrategyDecision}$$

---

## 2. Requirements Scope Matrix

### 2.1 Requirements Being Implemented in Phase 1
- **AF-REQ-001:** Deterministic Strategy Execution (pure functional design, zero random/clock dependencies).
- **AF-REQ-002:** Closed-Candle Rule Enforcement (`[0]` forming candle strictly isolated; signal generation uses only `[1]` and older).
- **AF-REQ-003:** Multi-Timeframe Signal Synchronization ($TF_{exec}$ lower execution timeframe + $TF_{conf}$ higher confirmation timeframe).
- **AF-REQ-004:** Index and Futures Dual Confirmation Interface (consuming formal `FuturesConfirmationStatus`).
- **AF-REQ-005:** Deterministic Single Entry, Single Stop-Loss, Single Target Trade Structure.
- **AF-REQ-006:** Strict Prohibition of Excluded Capabilities (no AI/ML, no news sentiment, no averaging down, no auto scaling).
- **AF-REQ-010:** Stale Data Rejection Gate (deterministic rejection code `REJECT_DATA_STALE`).
- **AF-REQ-036:** Deterministic Versioning and Canonical Configuration Hashing (`strategy_id`, `strategy_version`, `config_hash`).

### 2.2 Requirements Explicitly Deferred to Later Phases
- **Deferred to Phase 2 (Data Governance):** Ingestion pipelines, WebSocket handlers, real-time feed parsers, disk storage stores, auto-backfill on data gaps.
- **Deferred to Phase 3 (Contract Lifecycle):** Instrument master catalogs, expiry calendar lookup, monthly rollover scheduler.
- **Deferred to Phase 4 (Basis Engine):** Real-time basis spread calculation, basis point delta, rolling basis z-score computation engine. *(Phase 1 only consumes the abstract status interface)*.
- **Deferred to Phase 5 (Risk Engine):** Capital-based lot sizing math, account margin validation, daily loss circuit breakers, open exposure limits.
- **Deferred to Phase 6 (Cost & Slippage):** Exchange statutory charge calculation (STT, GST, brokerage) and bid-ask slippage deduction.
- **Deferred to Phase 7 (Order State Machine):** 17-state order lifecycle, emergency unprotected position watchdog, broker order submission.
- **Deferred to Phase 8 (Idempotency & Crash Recovery):** Broker adapters, network retry logic, 9-step cold boot reconciliation.
- **Deferred to Phase 9–17:** Audit ledger persistence, backtesting runner, replay engine, chaos injection, deployment, paper, and live trading.

---

## 3. Directory & Module Blueprint for Phase 1

```text
d:\AlphaForge\
├── alphaforge/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py            # Immutable Candle, Timeframe, Context models
│   │   ├── enums.py             # Direction, Decision, RejectionCode, TrendState
│   │   └── exceptions.py        # AlphaForge core domain exceptions
│   └── strategy/
│       ├── __init__.py
│       ├── config.py            # StrategyConfig with canonical JSON serialization & hashing
│       ├── indicators.py        # Pure vectorized indicator functions (EMA, ATR, Volume, etc.)
│       ├── rules.py             # Formal mathematical definitions of trend, breakout, pullback, trigger
│       ├── validator.py         # Signal validator and rejection gatekeeper
│       └── engine.py            # Deterministic Strategy Engine (pure function evaluation)
├── docs/
│   ├── PHASE_1_IMPLEMENTATION_PLAN.md
│   ├── STRATEGY_SPECIFICATION.md
│   ├── STRATEGY_RULE_CATALOG.md
│   ├── STRATEGY_SIGNAL_CONTRACT.md
│   ├── STRATEGY_DETERMINISM.md
│   └── PHASE_1_ACCEPTANCE_REPORT.md
└── tests/
    ├── __init__.py
    ├── golden/                  # Deterministic JSON fixtures and test cases
    │   ├── fixtures/
    │   └── test_golden_fixtures.py
    ├── property/                # Hypothesis property-based invariant tests
    │   └── test_strategy_properties.py
    └── unit/
        └── strategy/
            ├── test_determinism.py
            ├── test_closed_candle_isolation.py
            ├── test_multi_timeframe.py
            ├── test_rules.py
            └── test_rejection_paths.py
```

---

## 4. Formal Interfaces & Data Contracts

### 4.1 Immutable Candle Representation
The strategy core consumes frozen `Candle` structures containing:
- `timestamp`: UTC datetime of the candle close.
- `open`, `high`, `low`, `close`: `Decimal` fixed-point numbers.
- `volume`: `int`.
- `open_interest`: `int` or `None`.

### 4.2 Multi-Timeframe Synchronization Contract
- Primary execution series $S_{exec}$: List of candles where index `[0]` is current forming (quarantined), `[1]` is latest closed, `[2]` is previous closed.
- Higher confirmation series $S_{conf}$: List of candles on higher timeframe where only closed candles with `close_timestamp <= S_exec[1].timestamp` are eligible.

### 4.3 Futures Confirmation Status Enum
Consumed as an external gate:
```python
class FuturesConfirmationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    INVALID = "INVALID"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
```

### 4.4 Canonical Output Signal Schema
The engine produces an immutable `StrategySignal`:
- `signal_id`: Deterministic SHA-256 hash.
- `strategy_id`: `"AF_ORB_MOMENTUM_V1"`.
- `strategy_version`: `"1.0.0"`.
- `symbol`: string (e.g. `"NIFTY"`).
- `direction`: `LONG` | `SHORT` | `FLAT`.
- `signal_timestamp`: Timestamp of candle `[1]`.
- `evaluation_timestamp`: Timestamp of evaluation context.
- `entry_reference`: `Decimal`.
- `stop_reference`: `Decimal`.
- `target_reference`: `Decimal`.
- `risk_distance`: `Decimal` ($|\text{entry} - \text{stop}|$).
- `trend_state`: `BULLISH` | `BEARISH` | `NEUTRAL`.
- `basis_status`: `FuturesConfirmationStatus`.
- `volume_status`: string.
- `decision`: `ACCEPT` | `REJECT` | `INVALID_DATA` | `EXPIRED` | `DUPLICATE`.
- `rejection_code`: string enum (e.g. `REJECT_NONE`, `REJECT_TREND`, `REJECT_CLOSED_CANDLE_VIOLATION`, etc.).
- `config_hash`: Hex string.
- `data_version`: integer.

---

## 5. Formal Strategy Rule Architecture

The strategy implemented is the institutional **AlphaForge Trend-Breakout-Confirmation Strategy (V1)**:

1. **Trend Regime ($TF_{conf}$):**
   - Calculated on closed candles of higher timeframe.
   - Formula: $EMA_{fast}(15m) > EMA_{slow}(15m)$ and $Close_{[1]} > EMA_{slow}(15m) \implies \text{BULLISH}$.
   - Inverse $\implies \text{BEARISH}$. Otherwise $\implies \text{NEUTRAL}$.

2. **Support & Resistance ($TF_{exec}$):**
   - Dynamic reference: 20-period swing high / swing low over closed candles `[1..20]`.
   - Structural level must be strictly bounded by closed candle highs/lows.

3. **Breakout & Confirmation Candle Geometry ($TF_{exec}$):**
   - For LONG: Closed candle `[1]` high breaks above 20-period resistance.
   - Body Ratio: $\frac{|Close_{[1]} - Open_{[1]}|}{High_{[1]} - Low_{[1]}} \ge 0.50$ (minimum 50% solid body, preventing indecisive dojis).
   - Close Location: $Close_{[1]} \ge Low_{[1]} + 0.70 \times (High_{[1]} - Low_{[1]})$ (close in top 30% of range).

4. **Momentum Filter ($TF_{exec}$):**
   - RSI (14-period) on closed candles `[1..N]`.
   - LONG requires $RSI_{[1]} > 50.0$ and $RSI_{[1]} \le 75.0$ (momentum confirmed, not extreme overbought).
   - SHORT requires $RSI_{[1]} < 50.0$ and $RSI_{[1]} \ge 25.0$.

5. **Relative Volume Filter ($TF_{exec}$):**
   - $Volume_{[1]} \ge 1.20 \times \text{SMA}(Volume, 20)_{[1]}$ (volume spike confirming breakout).

6. **Futures Confirmation Interface:**
   - Must be strictly `FuturesConfirmationStatus.CONFIRMED`.

7. **Stop-Loss & Target Contract:**
   - LONG Stop: $\min(Low_{[1]}, Low_{[2]}) - 1.0 \times ATR(14)_{[1]}$ (or swing low buffer).
   - LONG Target: $Entry + 2.0 \times (Entry - Stop)$ ($1:2$ fixed risk-to-reward).
   - SHORT Stop: $\max(High_{[1]}, High_{[2]}) + 1.0 \times ATR(14)_{[1]}$.
   - SHORT Target: $Entry - 2.0 \times (Stop - Entry)$.

---

## 6. Testing & Acceptance Strategy

1. **Unit Tests:** Deterministic execution, pure indicator outputs, boundary logic.
2. **Forming Candle Quarantine Test:** Inject mutating variations into candle `[0]`; assert 100% identical signal output.
3. **Closed Candle Mutation Test:** Mutate candle `[1]`; assert strategy detects change and updates signal accordingly.
4. **Rejection Path Matrix:** Unit tests covering all 14 discrete rejection codes.
5. **Property Tests (`hypothesis`):** Fuzzing candle price bounds, verifying invariants (e.g. stop is always below entry for long).
6. **Golden Fixtures:** 14 static JSON scenario fixtures verifying bit-exact outputs.

---

## 7. Known Risks & Mitigations

| Risk | Mitigation |
| :--- | :--- |
| Floating point divergence across architectures | Use `Decimal` for all price, stop, target, and risk distance fields. |
| Inadvertent access to candle `[0]` | Strategy engine interface slices `candles[1:]` internally and raises error if `[0]` is referenced. |
| Non-deterministic hashing of config | Sort JSON keys and use UTF-8 encoded bytes for SHA-256 digest. |
