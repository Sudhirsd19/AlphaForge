# ALPHAFORGE — PHASE 1 FORENSIC RE-AUDIT REPORT

**Project Name:** AlphaForge  
**Audit Type:** Strict Read-Only Forensic Re-Audit  
**Phase Under Audit:** Phase 1 — Deterministic Strategy Specification  
**Auditor:** Principal Software Architect, Senior Quantitative Trading Systems Engineer, Reliability Engineer, Security Engineer, and Code Auditor  
**Date:** 2026-09-12  
**Audit Scope:** Full Phase 1 implementation (`alphaforge/`, `tests/`, `docs/`, `pyproject.toml`)  
**Baseline Authority:** Master Specification & Phase 0 Frozen Contracts (`docs/REQUIREMENT_FREEZE.md`, `docs/FORMAL_REQUIREMENT_MATRIX.md`, `docs/OPEN_DESIGN_DECISIONS.md`)

---

## 1. Executive Summary & Final Status

```text
================================================================================
FINAL CLASSIFICATION: PHASE 1 = BLOCKED
CAN PHASE 2 START:    NO
================================================================================
```

### Reason for BLOCKED Classification
While the Phase 1 codebase demonstrates mathematical determinism, pure functional architecture, and strict closed-candle isolation (`[1]` vs `[0]`), the implementation contains **21 unapproved strategy parameters and structural formulas** (including `Structural swing ± 1.0 × ATR` and `Fixed 1:2 R:R target`) that were introduced in Phase 1 without prior authorization in the Master Specification or Phase 0 Requirement Freeze. 

In strict accordance with Section 14 of the Forensic Re-Audit Directive, Phase 1 cannot be approved while unapproved strategy parameters exist.

---

## 2. Critical Parameter Forensics Table

Below is the exhaustive inventory of all 21 hard-coded or configured strategy parameters identified in the Phase 1 codebase:

| Parameter | Actual Implementation | Source Document | Frozen in Phase 0? | Approved? | Classification | Problem Statement |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Trend Fast EMA** | Period = 9 (`StrategyConfig.trend_ema_fast`) | `config.py:26` | No | No | `UNAPPROVED ASSUMPTION` | Period 9 was assumed without quantitative justification or user sign-off. |
| **Trend Slow EMA** | Period = 21 (`StrategyConfig.trend_ema_slow`) | `config.py:27` | No | No | `UNAPPROVED ASSUMPTION` | Period 21 was assumed without quantitative justification or user sign-off. |
| **Trend Regime Formula** | Fast > Slow & Close > Slow = Bullish; inverse = Bearish | `rules.py:38-43` | No | No | `UNAPPROVED ASSUMPTION` | Dual-EMA price relationship rule was invented without prior approval. |
| **Breakout Lookback** | Lookback = 20 bars (`StrategyConfig.breakout_lookback`) | `config.py:30` | No | No | `UNAPPROVED ASSUMPTION` | 20-period swing high/low lookback was assumed without prior approval. |
| **Breakout Condition** | $Close(C_1) > \max(High[2..21])$ | `rules.py:67-68` | No | No | `UNAPPROVED ASSUMPTION` | Close-based breakout rule was assumed without buffer or user approval. |
| **Min Body Ratio** | Ratio $\ge 0.50$ (`StrategyConfig.min_body_ratio`) | `config.py:33` | No | No | `UNAPPROVED ASSUMPTION` | 50% candle body ratio threshold was assumed without approval. |
| **Close Location Ratio** | Ratio $\ge 0.70$ (`StrategyConfig.min_close_location_ratio`) | `config.py:34` | No | No | `UNAPPROVED ASSUMPTION` | Top/bottom 30% close location requirement was assumed without approval. |
| **Volume Lookback** | Lookback = 20 bars (`StrategyConfig.volume_lookback`) | `config.py:37` | No | No | `UNAPPROVED ASSUMPTION` | 20-bar volume moving average was assumed without approval. |
| **Relative Volume Threshold** | Ratio $\ge 1.20$ (`StrategyConfig.min_relative_volume`) | `config.py:38` | No | No | `UNAPPROVED ASSUMPTION` | 1.20x volume spike threshold was assumed without approval. |
| **RSI Period** | Period = 14 (`StrategyConfig.rsi_period`) | `config.py:41` | No | No | `UNAPPROVED ASSUMPTION` | Standard 14-period RSI was assumed without approval. |
| **RSI Long Range** | $50.0 < RSI \le 75.0$ (`rsi_long_min/max`) | `config.py:42-43` | No | No | `UNAPPROVED ASSUMPTION` | 50-75 momentum window was assumed without approval. |
| **RSI Short Range** | $25.0 \le RSI < 50.0$ (`rsi_short_min/max`) | `config.py:44-45` | No | No | `UNAPPROVED ASSUMPTION` | 25-50 momentum window was assumed without approval. |
| **ATR Period** | Period = 14 (`StrategyConfig.atr_period`) | `config.py:48` | No | No | `UNAPPROVED ASSUMPTION` | Standard 14-period Wilder ATR was assumed without approval. |
| **ATR Stop Multiplier** | Multiplier = 1.0 (`StrategyConfig.atr_stop_multiplier`) | `config.py:49` | No | No | `UNAPPROVED ASSUMPTION` | 1.0 ATR buffer was assumed without approval. |
| **Min Volatility ATR %** | Min ATR = $0.05\%$ of price (`atr_min_pct`) | `config.py:50` | No | No | `UNAPPROVED ASSUMPTION` | 0.05% minimum volatility floor was assumed without approval. |
| **Max Volatility ATR %** | Max ATR = $1.50\%$ of price (`atr_max_pct`) | `config.py:51` | No | No | `UNAPPROVED ASSUMPTION` | 1.50% volatility ceiling was assumed without approval. |
| **Stop-Loss Formula** | $\min(Low[1], Low[2]) - 1.0 \times ATR$ (Long) | `rules.py:195-196`| No | No | `UNAPPROVED ASSUMPTION` | Structural swing of 2 candles $\pm 1.0$ ATR was invented without approval. |
| **Target Formula** | $Entry \pm 2.0 \times \text{RiskDistance}$ | `rules.py:198,203`| No | No | `UNAPPROVED ASSUMPTION` | Fixed 1:2 R:R formula was assumed without prior approval. |
| **Target R:R Multiple** | Multiple = 2.0 (`StrategyConfig.target_risk_multiple`) | `config.py:56` | No | No | `UNAPPROVED ASSUMPTION` | 2.0 multiple was assumed without approval. |
| **Min/Max Risk Dist %** | $0.10\% \le \text{RiskDist} \le 3.00\%$ of price | `config.py:54-55` | No | No | `UNAPPROVED ASSUMPTION` | Risk distance boundaries were assumed without approval. |
| **Max Stale Seconds** | 195 seconds (`StrategyConfig.max_stale_seconds`) | `config.py:59` | No | No | `PROPOSED — REQUIRES APPROVAL` | 180s + 15s grace was proposed but never formally approved. |
| **Timeframe Defaults** | Exec: 3m, Conf: 15m (`exec_timeframe`, `conf_timeframe`) | `config.py:22-23` | No | No | `PROPOSED — REQUIRES APPROVAL` | 3m/15m pair was proposed but never formally approved. |

---

## 3. Verification of Specific Unapproved Claims

### 3.1 Claim: "Structural swing $\pm 1.0 \times ATR$"
- **Forensic Verification:** The Master Specification (Section 5) specifies only "Single stop-loss". Phase 0 Requirement Freeze (`docs/REQUIREMENT_FREEZE.md` Section 4.1) specifies "Single stop-loss placed immediately upon fill". Neither document specifies the formula $\min(Low[1], Low[2]) \pm 1.0 \times ATR$.
- **Finding:** **UNAPPROVED ASSUMPTION**. While mathematically deterministic, the formula was invented in Phase 1 without user approval.

### 3.2 Claim: "Fixed 1:2 R:R target"
- **Forensic Verification:** The Master Specification (Section 5) specifies only "Single target". Phase 0 Requirement Freeze specifies "Single profit target". Neither document authorizes a fixed $1:2$ reward-to-risk ratio.
- **Finding:** **UNAPPROVED ASSUMPTION**. The $1:2$ target ratio is an unapproved quantitative assumption.

---

## 4. Determinism vs Validity vs Quantitative Proof

The Phase 1 implementation must be rigorously evaluated across four distinct engineering dimensions:

```text
+----------------------------+--------------------------------------------------------+
| Dimension                  | Forensic Status & Evidence                              |
+----------------------------+--------------------------------------------------------+
| 1. Deterministic           | VERIFIED (PASS): Same input produces bit-exact same     |
|                            | output across 100 runs. Decimal arithmetic used.        |
+----------------------------+--------------------------------------------------------+
| 2. Specification-Compliant | FAILED (BLOCKED): 21 strategy parameters and concrete  |
|                            | indicator formulas were invented without approval.      |
+----------------------------+--------------------------------------------------------+
| 3. Quantitatively Validated| NOT EVALUATED: 0 backtests, 0 walk-forward runs, 0      |
|                            | Monte Carlo simulations performed. No edge is claimed. |
+----------------------------+--------------------------------------------------------+
| 4. Production-Safe         | PRE-PRODUCTION: Risk engine (Phase 5), execution FSM    |
|                            | (Phase 7), and broker recovery (Phase 8) not yet built.|
+----------------------------+--------------------------------------------------------+
```

---

## 5. Closed-Candle Forensics (Rule 8)

- **Source Code Verification (`alphaforge/strategy/engine.py`):**
  - Line 83: `closed_exec = raw_exec_candles[1:]`
  - Line 84: `trigger_candle = closed_exec[0]` (strictly index `[1]` of input)
  - Line 85: `prior_candle = closed_exec[1]` (strictly index `[2]` of input)
  - Line 154: `chrono_closed = list(reversed(closed_exec))`
  - Lines 157–300: All calculations (breakout, geometry, volume, RSI, ATR, stops, targets) operate strictly on `closed_exec` or `chrono_closed`.
  - Line 380: `compute_signal_id()` uses `trigger_candle.timestamp`.
- **Forming Candle Leakage Check:**
  - Index `[0]` is stripped at line 83 and never accessed downstream.
  - Verified by `tests/unit/strategy/test_closed_candle_isolation.py` and `tests/property/test_strategy_properties.py`.
- **Finding:** **COMPLIANT (PASS)**. Closed-candle isolation is strictly enforced.

---

## 6. Multi-Timeframe Forensics

- **Timestamp Filtering (`alphaforge/strategy/engine.py` lines 109–111):**
  ```python
  eligible_conf = [c for c in raw_conf_candles if c.timestamp <= signal_timestamp]
  eligible_conf_closed = [c for c in eligible_conf if c.is_closed]
  ```
  - Ensures no higher-timeframe candle closing after $S_{exec}[1]$ can leak into evaluation.
- **Architectural Vulnerability Identified (Finding `AF-P2-01`):**
  - Line 109 does not sort `eligible_conf_closed`. If the caller supplies `raw_conf_candles` in reverse chronological order (matching `raw_exec_candles`), `evaluate_trend_regime` computes EMAs over reversed prices, leading to inverted trend detection.
- **Finding:** **P2 DEFECT**. Input sorting must be explicit.

---

## 7. Signal ID Forensics

- **Implementation (`alphaforge/strategy/engine.py` lines 380–388):**
  ```python
  payload = (
      f"{self.config.strategy_id}:{self.config.strategy_version}:{self.config.symbol}:"
      f"{direction.value}:{signal_timestamp.isoformat()}:{self.config_hash}"
  )
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
  ```
- **Dependencies Checked:**
  - Random UUID: None.
  - Wall-clock time: None (`signal_timestamp` is from candle `[1]`).
  - Process/Machine ID: None.
  - Memory address: None.
- **Finding:** **COMPLIANT (PASS)**. Signal ID is 100% deterministic and replayable.

---

## 8. Configuration Hash Forensics

- **Implementation (`alphaforge/strategy/config.py` lines 61–74):**
  - Canonical JSON serialization with sorted keys (`sort_keys=True`) and compact separators (`separators=(',', ':')`).
  - Decimal values converted to standardized string format before hashing.
  - Verified that altering any parameter changes the hash; identical parameters produce identical hash.
- **Vulnerability Identified (Finding `AF-P2-02`):**
  - The structural stop lookback (2 candles: `trigger_candle` and `prior_candle` in `rules.py:195,200`) is hardcoded in `rules.py` and is NOT a field in `StrategyConfig`. Consequently, altering the swing stop lookback does not alter `config_hash`.
- **Finding:** **P2 DEFECT**. All parameters affecting signal decisions must reside in `StrategyConfig`.

---

## 9. Rejection Path Forensics

- **Exhaustive Rejection Path Audit:**
  - 13 distinct rejection exit points in `DeterministicStrategyEngine.evaluate()`.
  - Every single path maps directly to an explicit, strongly typed `RejectionCode` member.
  - Zero generic or catch-all rejection codes.
  - Zero exceptions silently swallowed or converted to `ACCEPT`.
- **Finding:** **COMPLIANT (PASS)**.

---

## 10. Stop and Target Forensics

- **Mathematical Relationship Verification:**
  - For LONG: $Stop < Entry < Target$ strictly enforced.
  - For SHORT: $Target < Entry < Stop$ strictly enforced.
  - Risk distance strictly positive: checked at line 306.
  - Target multiple non-positive: rejected at line 322.
- **Specification Compliance Check:**
  - Stop formula ($\min(Low[1], Low[2]) - 1.0 \times ATR$): **UNAPPROVED ASSUMPTION**.
  - Target formula ($Entry + 2.0 \times \text{RiskDistance}$): **UNAPPROVED ASSUMPTION**.
- **Finding:** **BLOCKED due to Unapproved Assumptions**.

---

## 11. Test Quality Audit

- **Positive Findings:**
  - 37 tests executed in 0.75 seconds.
  - Hypothesis property tests verify forming candle `[0]` isolation across randomized prices.
  - Unit tests prove determinism across 100 repeated runs.
- **Identified Test Suite Deficiencies (Finding `AF-P2-03`):**
  1. **Circular Golden Fixtures:** The 14 golden fixtures in `tests/golden/test_golden_fixtures.py` are generated using synthetic formulas that embody the unapproved parameters rather than testing against an approved quantitative benchmark.
  2. **In-Memory Fixtures vs Static JSON:** Fixtures are generated in code via `build_scenario_candles()` rather than read from immutable, versioned static JSON files in `tests/golden/fixtures/*.json`.
  3. **Missing Reverse-Chronological Conf Candle Test:** No test verifies behavior when `raw_conf_candles` is passed in reverse-chronological order.
  4. **Missing Zero-Volume Test:** No test explicitly tests division-by-zero handling when all historical volume is zero.
- **Finding:** **P2 DEFECT**. Test suite must be hardened with static JSON fixtures.

---

## 12. Phase Boundary Audit

- **Verification Against Phases 2–17:**
  - Market data ingestion: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - Broker adapters / Zerodha API: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - Risk engine & lot sizing: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - Order state machine: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - Audit ledger persistence: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - Backtesting / Replay engines: **NOT IMPLEMENTED (0%)** (Clean boundary).
  - `LIVE_TRADING = FALSE` preserved: **VERIFIED**.
- **Finding:** **COMPLIANT (PASS)**. Zero phase boundary leakage.

---

## 13. Dependency & Environment Audit

- **Python Runtime:** Python 3.14.4 detected in local host environment.
- **Project Configuration (`pyproject.toml`):**
  - Configured target: `requires-python = ">=3.11"`, Ruff `target-version = "py311"`, Mypy `python_version = "3.11"`.
  - Python 3.14 has NOT been frozen as a hard runtime requirement.
- **Installed Dependencies in `.venv`:** `pydantic (2.13.5)`, `numpy (2.5.3)`, `hypothesis (6.168.0)`, `pytest (9.1.1)`.
- **Network Independence:** Verified that all dependencies operate entirely offline. Zero network calls exist in the test suite.
- **Finding:** **COMPLIANT (PASS)**.

---

## 14. Formal Findings Inventory

### P0 Findings (Catastrophic / Safety Critical)
*None.*

### P1 Findings (Critical Specification Violations)
* **Finding ID:** `AF-P1-01`  
  **Severity:** P1 (Critical Specification Violation)  
  **File:** `alphaforge/strategy/config.py`, `alphaforge/strategy/rules.py`  
  **Function/Class:** `StrategyConfig`, `calculate_stops_and_targets`, `evaluate_trend_regime`  
  **Issue:** 21 strategy parameters and formulas (including ATR 14, EMA 9/21, breakout 20, RSI bounds, swing stop formula, 1:2 R:R target) were implemented without prior authorization in the Master Specification or Phase 0 Requirement Freeze.  
  **Expected Behavior:** Specific indicator parameters must either be explicitly defined by the Master Specification or formally documented as `PROPOSED — REQUIRES APPROVAL` in `docs/OPEN_DESIGN_DECISIONS.md` and approved by the user before being frozen in code.  
  **Actual Behavior:** Parameters were directly codified in `config.py` and `rules.py` and presented as completed.  
  **Evidence:** `StrategyConfig` lines 26–59; `rules.py` lines 179–208.  
  **Required Action:** Formally catalog all 21 parameters in `docs/OPEN_DESIGN_DECISIONS.md` under a dedicated Strategy Parameter Decision clause and obtain explicit user approval before Phase 1 can be marked APPROVED.

### P2 Findings (High Correctness / Quality Issues)
* **Finding ID:** `AF-P2-01`  
  **Severity:** P2 (High Correctness Defect)  
  **File:** `alphaforge/strategy/engine.py`  
  **Function/Class:** `DeterministicStrategyEngine.evaluate` (lines 109–111)  
  **Issue:** `eligible_conf_closed` is not explicitly sorted by timestamp. If the caller supplies candles in reverse chronological order (matching `raw_exec_candles`), trend evaluation calculates EMAs over reversed prices.  
  **Expected Behavior:** `eligible_conf_closed` must be explicitly sorted chronologically before indicator evaluation: `sorted(eligible_conf_closed, key=lambda c: c.timestamp)`.  
  **Actual Behavior:** Filtered list retains input order.  
  **Evidence:** `alphaforge/strategy/engine.py` lines 109–112.  
  **Required Action:** Add explicit chronological sort to `eligible_conf_closed`.

* **Finding ID:** `AF-P2-02`  
  **Severity:** P2 (Architecture / Hash Invariant Defect)  
  **File:** `alphaforge/strategy/rules.py`  
  **Function/Class:** `calculate_stops_and_targets` (lines 195, 200)  
  **Issue:** Lookback window for structural swing (2 candles) is hardcoded and not configurable in `StrategyConfig`. Modifying this lookback does not change `config_hash`.  
  **Expected Behavior:** All parameters affecting price calculation must be exposed in `StrategyConfig` and included in `config_hash`.  
  **Actual Behavior:** Hardcoded `min(trigger_candle.low, prior_candle.low)` in `rules.py`.  
  **Evidence:** `rules.py` lines 195, 200.  
  **Required Action:** Expose `swing_stop_lookback: int = 2` in `StrategyConfig`.

* **Finding ID:** `AF-P2-03`  
  **Severity:** P2 (Testing Rigor Defect)  
  **File:** `tests/golden/test_golden_fixtures.py`  
  **Function/Class:** `build_scenario_candles`  
  **Issue:** Golden fixtures are generated dynamically via Python helper rather than loaded from immutable, static JSON files in `tests/golden/fixtures/*.json`.  
  **Expected Behavior:** Master Specification requires independent, tamper-evident static fixture files.  
  **Actual Behavior:** Fixtures are generated in-memory during test execution.  
  **Evidence:** `tests/golden/test_golden_fixtures.py` lines 40–195.  
  **Required Action:** Serialize all 14 scenario fixtures into standalone JSON files in `tests/golden/fixtures/`.

---

## 15. Required Corrective Actions for Phase 1 Approval

Before Phase 1 can transition from `BLOCKED` to `APPROVED`:
1. **User Parameter Approval:** User must review and approve (or adjust) the 21 strategy parameters listed in the Critical Parameter Forensics Table.
2. **Open Design Decisions Update:** Update `docs/OPEN_DESIGN_DECISIONS.md` to document all 21 parameters under formal decision entries with status `PROPOSED — REQUIRES APPROVAL`.
3. **Sort Confirmation Candles:** Add explicit chronological sorting to `eligible_conf_closed` in `engine.py`.
4. **Config Hash Completeness:** Move hardcoded 2-bar swing lookback into `StrategyConfig.swing_stop_lookback`.
5. **Static JSON Golden Fixtures:** Export all 14 scenario test fixtures into static JSON files under `tests/golden/fixtures/`.

---

## 16. Audit Conclusion & Stop Condition

```text
================================================================================
PHASE 1 FORENSIC RE-AUDIT CONCLUSION:
PHASE 1 = BLOCKED

DO NOT PROCEED TO PHASE 2.
NO CODE MODIFICATIONS PERFORMED (READ-ONLY AUDIT ENFORCED).
AWAITING USER REVIEW AND APPROVAL OF UNAPPROVED STRATEGY PARAMETERS.
================================================================================
```
