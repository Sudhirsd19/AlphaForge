# ALPHAFORGE — PHASE 1 REMEDIATION REPORT

**Project Name:** AlphaForge  
**Report Type:** Phase 1 Formal Remediation & Parameter Sign-Off Report  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Author:** Principal Quantitative Trading Systems Engineer, Security Engineer, Code Auditor  
**Date:** 2026-09-12  
**Baseline Authority:** Master Specification, Phase 0 Frozen Requirement Matrix, User Remediation Directive (2026-09-12)  
**Status:** **PHASE 1 = APPROVED**  

---

## 1. Approved Parameter Table (DEC-11)

In accordance with the User Directive of 2026-09-12, the following 21 parameters and formulas are formally signed-off as the **V1 deterministic strategy baseline**:

| Parameter / Rule | Approved V1 Value / Formula | Code Location | Category | Documented in DEC-11 |
| :--- | :--- | :--- | :--- | :--- |
| **Execution Timeframe** | `3m` (180s) | `config.py:22` | Core Timing | Approved |
| **Confirmation Timeframe** | `15m` (900s) | `config.py:23` | Core Timing | Approved |
| **Trend Fast EMA** | Period = `9` | `config.py:26` | Trend Filter | Approved |
| **Trend Slow EMA** | Period = `21` | `config.py:27` | Trend Filter | Approved |
| **Trend Long Regime** | $EMA_9 > EMA_{21} \land Close > EMA_{21}$ | `rules.py:38-43` | Trend Filter | Approved |
| **Trend Short Regime** | $EMA_9 < EMA_{21} \land Close < EMA_{21}$ | `rules.py:40-43` | Trend Filter | Approved |
| **Breakout Lookback** | Lookback = `20` closed bars | `config.py:30` | Breakout Engine | Approved |
| **Long Breakout Rule** | $Close(C_1) > \max(High[2..21])$ | `rules.py:67-68` | Breakout Engine | Approved |
| **Short Breakout Rule** | $Close(C_1) < \min(Low[2..21])$ | `rules.py:68-69` | Breakout Engine | Approved |
| **Min Candle Body Ratio**| Ratio $\ge 0.50$ | `config.py:33` | Candle Geometry | Approved |
| **Min Close Location Ratio**| Ratio $\ge 0.70$ (Top/bottom 30%) | `config.py:34` | Candle Geometry | Approved |
| **Volume Lookback** | Lookback = `20` closed bars | `config.py:37` | Volume Filter | Approved |
| **Relative Volume Threshold**| $Volume(C_1) \ge 1.20 \times \text{SMA}(Vol, 20)_{[2..21]}$ | `config.py:38` | Volume Filter | Approved |
| **RSI Period** | Period = `14` (Wilder smoothing) | `config.py:41` | Momentum Filter | Approved |
| **RSI Long Window** | $50.0 < RSI \le 75.0$ | `config.py:42-43`| Momentum Filter | Approved |
| **RSI Short Window** | $25.0 \le RSI < 50.0$ | `config.py:44-45`| Momentum Filter | Approved |
| **ATR Period** | Period = `14` (Wilder smoothing) | `config.py:48` | Volatility Filter | Approved |
| **Volatility Bounds** | $0.05\% \le \frac{ATR_{14}}{Price} \le 1.50\%$ | `config.py:50-51`| Volatility Filter | Approved |
| **Swing Stop Lookback** | Lookback = `2` closed bars (`swing_stop_lookback`)| `config.py:54` | Stop Loss | Approved |
| **ATR Stop Multiplier** | Multiplier = `1.0` | `config.py:49` | Stop Loss | Approved |
| **Long Stop Formula** | $P_{stop} = \min(Low[1], Low[2]) - 1.0 \times ATR$ | `rules.py:195-196`| Stop Loss | Approved |
| **Short Stop Formula** | $P_{stop} = \max(High[1], High[2]) + 1.0 \times ATR$| `rules.py:202-203`| Stop Loss | Approved |
| **Reward-to-Risk Ratio** | Fixed `1:2` target multiplier | `config.py:57` | Profit Target | Approved |
| **Long Target Formula** | $P_{target} = P_{entry} + 2.0 \times \text{RiskDistance}$ | `rules.py:198` | Profit Target | Approved |
| **Short Target Formula** | $P_{target} = P_{entry} - 2.0 \times \text{RiskDistance}$ | `rules.py:205` | Profit Target | Approved |
| **Risk Distance Bounds** | $0.10\% \le \frac{\text{RiskDistance}}{P_{entry}} \le 3.00\%$ | `config.py:55-56`| Risk Guardrails | Approved |
| **Max Stale Data Duration**| $195\text{ seconds}$ (180s candle + 15s grace) | `config.py:60` | Freshness Guard | Approved |

### Quantitative Disclaimer
These parameters are approved strictly as the **V1 strategy baseline**. No claims are made regarding profitability, parameter optimality, or statistical edge. Quantitative validation and parameter calibration belong exclusively to **Phase 10 (Backtest and Quant Validation)**. No AI/ML, curve-fitting, or sentiment indicators were added.

---

## 2. Remediations Executed

### 2.1 Remediate AF-P2-01: Higher-Timeframe Confirmation Ordering & Deduplication
- **Problem:** In `alphaforge/strategy/engine.py`, `eligible_conf_closed` was not explicitly sorted. Reverse-chronological input resulted in inverted EMA calculation.
- **Fix:**
  1. Confirmation candles are filtered for $timestamp \le signal\_timestamp$ and $is\_closed == True$.
  2. The filtered list is explicitly sorted chronologically with a deterministic secondary key tuple:
     ```python
     sorted_conf_closed = sorted(
         eligible_conf_closed,
         key=lambda c: (c.timestamp, c.open, c.high, c.low, c.close, c.volume),
     )
     ```
  3. Duplicate timestamps are deterministically deduplicated by preserving the first occurrence.
  4. Caller collections are completely untouched (pure function).
- **Regression Verification:** Added `test_confirmation_ordering_invariance()` in `tests/unit/strategy/test_forensic_regressions.py` proving that forward, reversed, and interleaved confirmation candle sequences produce identical signals.

### 2.2 Remediate AF-P2-02: Hardcoded Swing Stop Lookback Exposed & Hashed
- **Problem:** Structural swing lookback (2 bars) was hardcoded in `rules.py:195,200` and omitted from `StrategyConfig` and `config_hash`.
- **Fix:**
  1. Added `swing_stop_lookback: int = Field(default=2, gt=0)` to `StrategyConfig`.
  2. Because `compute_config_hash()` iterates over `self.model_dump()`, `swing_stop_lookback` is automatically serialized into canonical JSON and included in `config_hash`.
  3. Updated `calculate_stops_and_targets` in `alphaforge/strategy/rules.py` to accept `swing_candles: Candle | Sequence[Candle]`.
  4. Updated `engine.py` to pass `closed_exec[:self.config.swing_stop_lookback]`.
  5. Updated `engine.py` minimum history guard to account for `swing_stop_lookback + 1`.
- **Regression Verification:**
  - Added `test_config_hash_sensitivity_all_parameters()` proving changing `swing_stop_lookback` modifies `config_hash`.
  - Added `test_swing_stop_lookback_functional_effect()` proving lookback=4 includes deeper swing lows than lookback=2.

### 2.3 Remediate AF-P2-03: Static Immutable JSON Golden Fixtures
- **Problem:** Golden fixtures were dynamically constructed in-memory inside `tests/golden/test_golden_fixtures.py`.
- **Fix:**
  1. Generated 14 immutable, static JSON files under `tests/golden/fixtures/*.json`:
     - `scenario_01_valid_long.json`
     - `scenario_02_valid_short.json`
     - `scenario_03_trend_rejection.json`
     - `scenario_04_breakout_rejection.json`
     - `scenario_05_volume_rejection.json`
     - `scenario_06_momentum_rejection.json`
     - `scenario_07_volatility_rejection.json`
     - `scenario_08_futures_rejection.json`
     - `scenario_09_stale_data.json`
     - `scenario_10_invalid_data.json`
     - `scenario_11_expired_setup.json`
     - `scenario_12_invalid_stop.json`
     - `scenario_13_invalid_target.json`
     - `scenario_14_duplicate_signal.json`
  2. Rewrote `test_golden_fixtures.py` to load fixtures strictly from disk using `Path(__file__).parent / "fixtures" / ...`.
  3. Zero fixture generation remains inside the test code.
  4. Zero network, zero wall-clock, zero random dependencies.

---

## 3. Forensic Regression Tests Added

Added dedicated test suite: [`tests/unit/strategy/test_forensic_regressions.py`](file:///d:/AlphaForge/tests/unit/strategy/test_forensic_regressions.py) containing 10 comprehensive tests:

1. **Closed Candle Quarantine:** Proves mutating forming candle `[0]` with extreme prices produces zero difference in signal.
2. **Confirmation Ordering Invariance:** Proves normal, reversed, and interleaved confirmation inputs evaluate identically.
3. **Confirmation Duplicate Timestamps:** Proves duplicate timestamps in confirmation feed are handled deterministically.
4. **Configuration Hash Sensitivity:** Proves mutating every single one of the 21 parameters alters `config_hash`.
5. **Swing Stop Lookback Effect:** Proves stop calculation actually consumes `swing_stop_lookback`.
6. **Directional SL & Target Orientation:** Validates $Stop < Entry < Target$ (Long) and $Target < Entry < Stop$ (Short).
7. **Reward-to-Risk Exactness:** Validates target distance is bit-exact $2.0 \times \text{RiskDistance}$.
8. **Risk Distance Guardrails:** Validates rejection for risk distances $<0.10\%$ and $>3.00\%$.
9. **Stale Data Threshold:** Validates acceptance at $195\text{s}$ and rejection at $196\text{s}$.
10. **Mathematical Determinism (100 Runs):** Validates identical outputs across 100 repeated executions.

---

## 4. Full Test Results

```text
============================= test session starts =============================
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\AlphaForge
configfile: pyproject.toml
testpaths: tests
plugins: hypothesis-6.168.0
collected 47 items

tests\golden\test_golden_fixtures.py ..............                      [ 29%]
tests\property\test_strategy_properties.py ..                            [ 34%]
tests\unit\strategy\test_closed_candle_isolation.py ..                   [ 38%]
tests\unit\strategy\test_determinism.py ..                               [ 42%]
tests\unit\strategy\test_forensic_regressions.py ..........              [ 63%]
tests\unit\strategy\test_indicators.py .......                           [ 78%]
tests\unit\strategy\test_rejection_paths.py ......                       [ 91%]
tests\unit\strategy\test_rules.py ....                                   [100%]

============================= 47 passed in 0.99s ==============================
```

---

## 5. Static Analysis, Linting & Type Checking

### 5.1 Ruff Linting
- **Command:** `.\.venv\Scripts\ruff check alphaforge tests`
- **Output:** `All checks passed!` (0 errors).

### 5.2 Ruff Formatting
- **Command:** `.\.venv\Scripts\ruff format --check alphaforge tests`
- **Output:** `19 files already formatted` (100% compliant with line length 100).

### 5.3 Mypy Type Checking (Strict Mode)
- **Command:** `.\.venv\Scripts\mypy --python-version 3.12 alphaforge tests --explicit-package-bases`
- **Output:** `Success: no issues found in 19 source files` (0 errors).
- **Core Package:** `.\.venv\Scripts\mypy alphaforge` $\to$ `Success: no issues found in 10 source files`.

---

## 6. Phase Boundary Verification

| Phase | Boundary Condition | Verified Status |
| :--- | :--- | :--- |
| **Phase 2** (Data Governance) | No parquet/duckdb persistence or live market adapters | **0% implemented** (Clean) |
| **Phase 3** (Contract Master) | No instrument lifecycle or expiry switching engines | **0% implemented** (Clean) |
| **Phase 4** (Basis Engine) | No basis anomaly calculation or z-score estimators | **0% implemented** (Clean) |
| **Phase 5** (Risk Engine) | No account capital checks, max drawdown, or lot sizing | **0% implemented** (Clean) |
| **Phase 6** (Cost Model) | No slippage or transaction cost models | **0% implemented** (Clean) |
| **Phase 7** (Order FSM) | No 17-state order lifecycle machine | **0% implemented** (Clean) |
| **Phase 8** (Idempotency) | No broker reconnect or crash recovery journal | **0% implemented** (Clean) |
| **Phase 9** (Audit Ledger) | No cryptographic event chain persistence | **0% implemented** (Clean) |
| **Phase 10-17** | No backtest, replay, paper, or live trading connections | **0% implemented** (Clean) |
| **Safety Lock** | `LIVE_TRADING = FALSE` strictly enforced | **VERIFIED** |

---

## 7. Remaining Risks & Phase 10 Handoff

1. **Market Edge / Robustness:** As stated in the Quantitative Disclaimer, the V1 parameters (EMA 9/21, breakout 20, RSI 50–75, 1:2 R:R) are unvalidated for historical profitability. They serve exclusively as a deterministic benchmark for testing the execution, risk, and recovery pipelines in Phases 2–9. Full quantitative validation, parameter sensitivity analysis, and regime testing will be conducted in Phase 10.
2. **Duplicate Signal Cache Scope:** The engine currently maintains an in-memory `_emitted_signal_ids` set. In Phase 8 (Idempotency and Crash Recovery), this deduplication cache will be backed by SQLite WAL persistence to survive process restarts.

---

## 8. Final Phase 1 Classification

```text
================================================================================
FINAL CLASSIFICATION: PHASE 1 = APPROVED
REASON:
  - All 21 V1 strategy parameters formally signed-off under DEC-11.
  - AF-P2-01 (Confirmation ordering) remediated and verified.
  - AF-P2-02 (Configurable swing stop lookback) remediated and verified.
  - AF-P2-03 (Static JSON golden fixtures) remediated and verified.
  - 47/47 automated unit, property, golden, and regression tests passing.
  - Ruff lint (0 errors), Ruff format (100% compliant), Mypy strict (0 errors).
  - 100% phase boundary isolation preserved; LIVE_TRADING = FALSE.
================================================================================
```
