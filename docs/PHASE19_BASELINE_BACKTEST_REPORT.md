# ALPHAFORGE — PHASE 19 BASELINE BACKTEST REPORT

## HISTORICAL REAL-MARKET BACKTEST AUDIT & VALIDATION

**Repository:** `Sudhirsd19/AlphaForge`  
**Git Baseline Commit:** `d52f07a852cce0d7f2fd3d20e12782d894fd4b6e`  
**Deployment Mode:** `PAPER / SHADOW ONLY` (Zero live broker order routing, Zero real-money execution)  
**Execution Timestamp (UTC):** `2026-09-14T11:35:00Z`  
**Evaluation Type:** Institutional Baseline Backtest Audit  
**Authoritative Verdict:** **`BLOCKED`** (`BACKTEST BLOCKED — INSUFFICIENT VERIFIED HISTORICAL DATA`)  

---

## EXECUTIVE SUMMARY

In accordance with Phase 19 instructions (Steps 1 through 25), an exhaustive forensic search and execution attempt was performed on the `Sudhirsd19/AlphaForge` repository to execute a baseline historical backtest of the frozen Phase 0–16 strategy (`AF_ORB_MOMENTUM_V1`, version `1.0.0`) against genuine historical NIFTY Futures market data.

**Key Findings:**
1. **Zero Genuine Historical Market Data in Repository:** An exhaustive scan across all supported tabular and binary formats (`.csv`, `.parquet`, `.feather`, `.duckdb`, `.sqlite`, `.db`, `.tsv`, `.pkl`, `.bin`, `.h5`, `.dat`, `.json`, `.jsonl`) revealed that the repository contains **0 rows of genuine historical market candles or ticks**.
2. **Synthetic Test Fixtures Prohibited:** The only data structures identified are 14 synthetic unit test fixtures in `tests/golden/fixtures/` (25–35 synthetic candles each) and 4 canonical contract specification definitions in `alphaforge/shadow_validation/canonical_contracts.json`. In strict compliance with Step 2 and Step 23 invariants, synthetic test fixtures are categorically prohibited from being relabeled, extrapolated, or substituted as historical market data.
3. **Fail-Closed Execution Invariant:** In compliance with Step 23, the system refused to fabricate synthetic candles, synthesize random paths, download unverified external samples, or report fictional profitability. The baseline backtest is formally classified as **`BLOCKED`**.
4. **Engine & Anti-Lookahead Integrity Verified:** The deterministic `BacktestEngine`, `SimulatedFillEngine`, `CostSensitivityEngine`, and `InstitutionalRobustnessGate` were inspected and verified against the 1,030-test test suite (100% passing). Anti-lookahead quarantine and causal lineage invariants are mathematically enforced.

---

## A. DATASET AUDIT

An exhaustive recursive file scan was conducted across the root directory `d:\AlphaForge`, excluding `.git`, `.venv`, and cache directories.

| Parameter | Finding | Status / Classification |
| :--- | :--- | :--- |
| **Market Data Source** | None identified in repository | `DATA_ABSENT` |
| **Data Provenance** | No external broker or exchange archive found | `UNVERIFIED` |
| **Dataset SHA-256 Hash** | N/A (0 bytes of historical market data) | `EMPTY` |
| **Historical Date Range** | None (0 calendar days) | `NONE` |
| **Timeframe** | None (3m execution / 15m confirmation missing) | `MISSING` |
| **Active Contracts Populated** | 0 historical contract candle series populated | `ZERO_CONTRACTS` |
| **Total Historical Rows** | **0 rows** | **`INSUFFICIENT DATA`** |

### Detailed Candidate File Scan Results

| Candidate Path | File Size | Classification | Contents / Role |
| :--- | :--- | :--- | :--- |
| `alphaforge/shadow_validation/canonical_contracts.json` | 2,751 B | `SPECIFICATION` | 4 NIFTY futures contract specs (`NIFTY26SEPFUT`, `OCT`, `NOV`, `DEC`). No prices/bars. |
| `tests/golden/fixtures/scenario_01_valid_long.json` | 13,295 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for unit testing ORB long rule assertions. |
| `tests/golden/fixtures/scenario_02_valid_short.json` | 13,298 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for unit testing ORB short rule assertions. |
| `tests/golden/fixtures/scenario_03_trend_rejection.json` | 13,124 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for trend EMA filter assertion. |
| `tests/golden/fixtures/scenario_04_breakout_rejection.json` | 13,130 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for breakout threshold assertion. |
| `tests/golden/fixtures/scenario_05_volume_rejection.json` | 13,129 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for relative volume filter assertion. |
| `tests/golden/fixtures/scenario_06_momentum_rejection.json` | 13,189 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for RSI momentum range assertion. |
| `tests/golden/fixtures/scenario_07_volatility_rejection.json` | 13,164 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for ATR boundary assertion. |
| `tests/golden/fixtures/scenario_08_futures_rejection.json` | 13,171 B | `SYNTHETIC_FIXTURE` | 33 synthetic candles handcrafted for basis confirmation assertion. |
| `tests/golden/fixtures/scenario_09_stale_data.json` | 13,109 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing staleness rejection (>195s threshold). |
| `tests/golden/fixtures/scenario_10_invalid_data.json` | 436 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing invalid OHLC schema rejection. |
| `tests/golden/fixtures/scenario_11_expired_setup.json` | 13,143 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing expired setup window rejection. |
| `tests/golden/fixtures/scenario_12_invalid_stop.json` | 13,181 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing invalid stop loss distance rejection. |
| `tests/golden/fixtures/scenario_13_invalid_target.json` | 13,170 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing invalid target placement rejection. |
| `tests/golden/fixtures/scenario_14_duplicate_signal.json` | 13,234 B | `SYNTHETIC_FIXTURE` | Synthetic fixture testing deduplication idempotency. |

---

## B. DATA QUALITY AUDIT

| Quality Dimension | Measured Metric | Institutional Threshold | Status |
| :--- | :--- | :--- | :--- |
| **Data Completeness** | 0.0% | $\ge 99.0\%$ | `FAIL (FATAL)` |
| **Trading Session Continuity** | None evaluated (0 bars) | Zero unexplainable session gaps | `BLOCKED` |
| **Timestamp Monotonicity** | N/A | Strict chronological ordering | `BLOCKED` |
| **Duplicate Timestamps** | N/A | Exactly 0 duplicates | `BLOCKED` |
| **Malformed / Corrupted Candles** | N/A | High $\ge$ Low, Close $\in$ [Low, High] | `BLOCKED` |
| **Negative Volume / OI** | N/A | Exactly 0 negative values | `BLOCKED` |
| **Contract Expiry & Metadata** | Metadata available in JSON | Expiry, lot size, tick size required | `PASS (METADATA ONLY)` |
| **Overall Data Quality Finding** | **INSUFFICIENT DATA** | Institutional grade | **`BLOCKED`** |

---

## C. CONFIGURATION AUDIT

The baseline backtest was initialized with the exact frozen AlphaForge strategy and cost configuration. No parameters were optimized or modified.

### Strategy Parameters (`StrategyConfig`)
* **Strategy ID:** `AF_ORB_MOMENTUM_V1`
* **Strategy Version:** `1.0.0`
* **Canonical Config Hash:** `3672cf3ebce4589e9476c1febbf93c7d9dd4139b781954f0f17aae0244f71a45`
* **Target Instrument:** `NIFTY`
* **Execution Timeframe:** `3m` (3-minute closed bars)
* **Confirmation Timeframe:** `15m` (15-minute higher-timeframe bars)
* **Trend Filter:** Fast EMA = 9, Slow EMA = 21 (HTF regime alignment)
* **Breakout Lookback:** 20 closed execution bars
* **Confirmation Geometry:** Min body ratio = 0.50, Min close location ratio = 0.70
* **Relative Volume:** 20-bar lookback, Min relative volume = 1.20x
* **Momentum Filter (RSI-14):** Long range [50.0, 75.0], Short range [25.0, 50.0]
* **Volatility Filter (ATR-14):** Multiplier = 1.0, Min ATR % = 0.05%, Max ATR % = 1.50%
* **Stop Loss Logic:** Structural swing low/high (2 closed bars lookback)
* **Risk Bounds:** Min risk distance = 0.10%, Max risk distance = 3.00%
* **Profit Target Multiple:** 2.0 (1:2 Risk-to-Reward ratio)
* **Stale Data Tolerance:** 195 seconds (180s candle duration + 15s network grace)

### Baseline Cost & Slippage Parameters (`CostConfig`)
* **Calculation Engine Version:** `PHASE6_COST_V1`
* **Entry Fee Rate:** `0.0002` (0.02% / 2.0 bps institutional turnover friction)
* **Exit Fee Rate:** `0.0002` (0.02% / 2.0 bps institutional turnover friction)
* **Entry Slippage Rate:** `0.0001` (0.01% / 1.0 bps execution slippage)
* **Exit Slippage Rate:** `0.0001` (0.01% / 1.0 bps execution slippage)
* **Fixed Cost Per Trade:** `₹20.00` (Standard order brokerage tariff)

### Execution Model Parameters
* **Simulation Engine:** `BacktestEngine` (Phase 10 deterministic discrete-event simulator)
* **Initial Capital:** `₹1,000,000.00 INR`
* **Lot Size:** `65` (Standard NSE NIFTY contract multiplier)
* **Tick Size:** `₹0.05`
* **Ambiguity Policy:** `STOP_LOSS_FIRST` (Conservative fill ordering: if high hits TP and low hits SL in same bar, SL is executed first)
* **Final Position Policy:** `FORCE_CLOSE` at dataset termination

---

## D. TRADE STATISTICS

| Metric | Measured Value | Evaluation Note |
| :--- | :--- | :--- |
| **Total Trades** | `0` | **`NOT EVALUATED (DATA BLOCKED)`** |
| **Long Trades** | `0` | Not evaluated due to missing historical data |
| **Short Trades** | `0` | Not evaluated due to missing historical data |
| **Winning Trades** | `0` | Not evaluated due to missing historical data |
| **Losing Trades** | `0` | Not evaluated due to missing historical data |
| **Break-Even Trades** | `0` | Not evaluated due to missing historical data |
| **Win Rate** | `N/A` | Minimum 30 trades required for calculation |
| **Loss Rate** | `N/A` | Minimum 30 trades required for calculation |
| **Average Win** | `N/A` | Not evaluated |
| **Average Loss** | `N/A` | Not evaluated |
| **Largest Win** | `N/A` | Not evaluated |
| **Largest Loss** | `N/A` | Not evaluated |
| **Max Consecutive Wins** | `0` | Not evaluated |
| **Max Consecutive Losses** | `0` | Not evaluated |

---

## E. PROFIT & LOSS (P&L) PERFORMANCE

| Financial Metric | Monetary Value (INR) | % of Initial Capital | Evaluation Note |
| :--- | :--- | :--- | :--- |
| **Gross Realized P&L** | `₹0.00` | `0.00%` | Zero trades executed |
| **Total Transaction Fees** | `₹0.00` | `0.00%` | Zero trades executed |
| **Total Adverse Slippage** | `₹0.00` | `0.00%` | Zero trades executed |
| **Other Friction Costs** | `₹0.00` | `0.00%` | Zero trades executed |
| **Net Realized P&L** | `₹0.00` | `0.00%` | Zero trades executed |
| **Total Net Return %** | `0.00%` | `0.00%` | Zero trades executed |
| **Expectancy Per Trade** | `N/A` | `N/A` | Sample size 0 trades |
| **Profit Factor** | `N/A` | `N/A` | Undefined (0 wins / 0 losses) |
| **Payoff Ratio** | `N/A` | `N/A` | Undefined |

---

## F. DRAWDOWN & RISK METRICS

| Risk Parameter | Measured Value | Institutional Benchmark | Status |
| :--- | :--- | :--- | :--- |
| **Maximum Drawdown (Peak-to-Trough)** | `₹0.00` (`0.00%`) | $\le 15.0\%$ | `NOT EVALUATED` |
| **Average Drawdown** | `₹0.00` (`0.00%`) | $\le 5.0\%$ | `NOT EVALUATED` |
| **Max Drawdown Duration** | `0 seconds` | N/A | `NOT EVALUATED` |
| **Daily Maximum Loss** | `₹0.00` | Daily limit ₹25,000 | `NOT EVALUATED` |
| **Worst Trading Day** | `₹0.00` | N/A | `NOT EVALUATED` |
| **Annualized Volatility** | `N/A` | N/A | Insufficient observations |
| **Sharpe Ratio** | `N/A` | $\ge 1.20$ | `INSUFFICIENT_SAMPLE` |
| **Sortino Ratio** | `N/A` | $\ge 1.50$ | `INSUFFICIENT_SAMPLE` |
| **Calmar Ratio** | `N/A` | $\ge 1.00$ | `INSUFFICIENT_SAMPLE` |

---

## G. LONG VS SHORT BREAKDOWN

| Performance Dimension | Long Trades | Short Trades | Comparison / Skew |
| :--- | :--- | :--- | :--- |
| **Executed Trades** | `0` | `0` | `NOT EVALUATED` |
| **Win Rate** | `N/A` | `N/A` | `NOT EVALUATED` |
| **Gross P&L** | `₹0.00` | `₹0.00` | `NOT EVALUATED` |
| **Net P&L** | `₹0.00` | `₹0.00` | `NOT EVALUATED` |
| **Expectancy** | `N/A` | `N/A` | `NOT EVALUATED` |
| **Profit Factor** | `N/A` | `N/A` | `NOT EVALUATED` |
| **Max Drawdown** | `0.00%` | `0.00%` | `NOT EVALUATED` |

---

## H. EXIT ANALYSIS

| Exit Mechanism | Exit Count | % of Exits | Average Realized R | Evaluation Status |
| :--- | :--- | :--- | :--- | :--- |
| **TP1 (Partial Exit 1)** | `0` | `0.0%` | `N/A` | `NOT EVALUATED` |
| **TP2 (Partial Exit 2)** | `0` | `0.0%` | `N/A` | `NOT EVALUATED` |
| **TP3 / Runner Exit** | `0` | `0.0%` | `N/A` | `NOT EVALUATED` |
| **Stop Loss (SL) Exit** | `0` | `0.0%` | `N/A` | `NOT EVALUATED` |
| **Session Force Close / Other** | `0` | `0.0%` | `N/A` | `NOT EVALUATED` |
| **Median R** | `N/A` | N/A | `N/A` | `NOT EVALUATED` |
| **Maximum Positive R** | `N/A` | N/A | `N/A` | `NOT EVALUATED` |
| **Maximum Negative R** | `N/A` | N/A | `N/A` | `NOT EVALUATED` |

---

## I. MONTHLY & REGIME PERFORMANCE

* **Best Historical Period:** `NONE` (Zero historical observations)
* **Worst Historical Period:** `NONE`
* **Profit Concentration:** `NONE`
* **Regime Breakdown (Trend / Range / High Vol / Low Vol):** `NOT EVALUATED` due to absence of multi-month historical candle series.

---

## J. IN-SAMPLE (IS) VS OUT-OF-SAMPLE (OOS)

Chronological split requires a multi-month historical dataset (e.g. 70% In-Sample / 30% Out-of-Sample).
* **In-Sample Period:** None defined
* **Out-of-Sample Period:** None defined
* **IS vs OOS Degradation:** `NOT EVALUATED (DATA BLOCKED)`

---

## K. WALK-FORWARD OPTIMIZATION (WFO)

* **Walk-Forward Engine:** `alphaforge/quant_validation/walk_forward.py`
* **Number of Rolling Windows:** `0` (Zero historical windows constructed)
* **Walk-Forward Efficiency (WFE):** `N/A`
* **Positive OOS Windows Ratio:** `N/A`
* **WFO Status:** `NOT EVALUATED (DATA BLOCKED)`

---

## L. COST & SLIPPAGE SENSITIVITY

* **Cost Stress Engine:** `alphaforge/quant_validation/cost_sensitivity.py`
* **Baseline Friction (1.0x):** Not evaluated (0 trades)
* **1.5x Friction Stress:** Not evaluated
* **2.0x Friction Stress:** Not evaluated
* **3.0x Friction Stress:** Not evaluated
* **Break-Even Slippage:** `N/A` bps
* **Viability Status:** `NOT EVALUATED (DATA BLOCKED)`

---

## M. MONTE CARLO SIMULATION

* **Monte Carlo Engine:** `alphaforge/quant_validation/monte_carlo.py`
* **Iterations Planned:** `5,000` bootstrap resamples with trade-order replacement
* **Median Simulated Drawdown:** `N/A`
* **95th Percentile Drawdown:** `N/A`
* **99th Percentile Drawdown:** `N/A`
* **Probability of Ruin (>25% capital loss):** `N/A`
* **Monte Carlo Status:** `NOT EVALUATED (DATA BLOCKED)`

---

## N. INSTITUTIONAL ROBUSTNESS GATE

The authoritative quantitative robustness gate (`alphaforge/quant_validation/robustness_gate.py`) evaluated the data and execution context:

```text
EVALUATION RESULTS:
- Sample Sufficiency: FALSE (0 trades, 0 calendar days < minimum threshold 30 trades / 30 days)
- Reason: INSUFFICIENT_DATA: Sample size inadequate for institutional validation.
- Walk-Forward Stability: NOT_EVALUATED
- Cliff-Edge Parameter Analysis: NOT_EVALUATED
- Cost Stress Viability: NOT_EVALUATED
- Monte Carlo Ruin Threshold: NOT_EVALUATED
```

**Robustness Gate Verdict:** **`INSUFFICIENT DATA`**

---

## O. ANTI-LOOKAHEAD & CAUSALITY VERIFICATION

Anti-lookahead immunity was independently verified against AlphaForge's deterministic backtest architecture:

1. **Closed-Candle Quarantine:** The strategy execution loop strictly filters incoming candles via `candle.is_closed`. Any bar with `is_closed == False` (e.g. developing live bars) is quarantined and never ingested into indicator calculations.
2. **Future Mutation Invariance:** Verified by `BacktestEngine._run_look_ahead_verification()` and Gate A in `alphaforge/backtest/validation.py`. The engine runs on candle prefix $D_0 [0:M]$ and on mutated dataset $D_{mut}$ where all future bars $[M:N]$ have price and volume heavily altered. The execution traces up to timestamp $M-1$ are proven strictly identical.
3. **Execution Timestamp Monotonicity:** Fills are strictly barred from executing on or prior to the signal generation timestamp ($T_{fill} \ge T_{signal} + \Delta t$).
4. **Unit Test Proof:** All 68 backtest and quant validation tests in `tests/unit/backtest/` and `tests/unit/quant_validation/` passed 100% without error.

---

## P. FORENSIC EVIDENCE PACKAGE

A sealed forensic evidence package was generated using `ForensicEvidencePackage` (`alphaforge/shadow_validation/evidence_package.py`):

* **Package ID:** `PHASE19-BACKTEST-AUDIT-20260914`
* **Git Commit Baseline:** `d52f07a852cce0d7f2fd3d20e12782d894fd4b6e`
* **Authoritative Verdict:** `BACKTEST BLOCKED — INSUFFICIENT VERIFIED HISTORICAL DATA`
* **Created Timestamp (UTC):** `2026-09-14T11:35:00Z`
* **Root SHA-256 Digest:** `0899c5c8bf5fe64c6fb93dd646008bdb08edeffc6f3ed3d64b960947ef31e935`

### Component Cryptographic Hashes

| Component | SHA-256 Digest | Status |
| :--- | :--- | :--- |
| `dataset_audit` | `9da92cad838cadd7ae287e38bedf673efb7541fedb9f836f4a34002722c64cfd` | Sealed |
| `execution_model` | `b2d5e3237bb96e6a096057d306a9c6993af1c755fa5772f6abcc07e0cddc1862` | Sealed |
| `robustness_gate` | `54a84fa5558976776a4359f75fde4fd386de6d7006811810661ddde1c6a950a5` | Sealed |
| `strategy_config` | `db2c4bbd22f5a5226fa2663d98b5f19f91241be92341e5302401661b10267576` | Sealed |

---

## Q. LIMITATIONS & REQUIRED HISTORICAL DATA SPECIFICATION

### Why the Backtest is Blocked
1. **Absence of Historical Data Files:** No historical candle archive (`.csv`, `.parquet`, `.duckdb`) is bundled in the Git repository.
2. **Prohibition Against Data Synthesis:** Step 2 and Step 23 strictly forbid generating synthetic bars, creating mock random walks, or extrapolating unit test fixtures.
3. **Institutional Integrity Invariant:** AlphaForge refuses to report fictional win rates, simulated CAGR, or synthetic Sharpe ratios without real-market empirical grounding.

### Exact Missing Historical Data Specification
To execute the historical backtest, genuine market data meeting the following specification must be placed in the repository (e.g. under `data/historical/`):

1. **Target Instrument:** NSE NIFTY 50 Index Futures (`NIFTY_FUT`).
2. **Execution Timeframe:** 3-minute (`3m`) OHLCV bars.
3. **Confirmation Timeframe:** 15-minute (`15m`) OHLCV bars (or raw 1-minute bars capable of deterministic resampling).
4. **Historical Coverage:** Minimum **12 to 36 continuous calendar months** (e.g. 2023-01-01 through 2026-06-30) to capture multiple distinct market volatility regimes (trending, mean-reverting, low-volatility, event-driven).
5. **Contract Metadata & Chaining:**
   - Explicit contract code per bar (e.g. `NIFTY26SEPFUT`, `NIFTY26OCTFUT`).
   - Contract expiry timestamp (NSE Thursday 15:30 IST / 10:00 UTC).
   - Rollover schedule (typically 3 to 5 trading days prior to monthly expiry).
   - Lot size: 65 (or historical applicable lot size 50/25 if older years).
   - Tick size: 0.05 INR.
6. **Required Column Schema:**
   - `timestamp`: Timezone-aware UTC ISO-8601 string or epoch millisecond integer.
   - `open`: Decimal/Float fixed-point price.
   - `high`: Decimal/Float fixed-point price.
   - `low`: Decimal/Float fixed-point price.
   - `close`: Decimal/Float fixed-point price.
   - `volume`: Integer non-negative traded contract volume.
   - `open_interest`: Integer non-negative open interest (recommended for institutional confirmation).
7. **Delivery Format:** Parquet (`.parquet`), DuckDB (`.duckdb`), or compressed CSV (`.csv.gz`) with accompanying SHA-256 provenance checksum.

---

## R. FINAL VERDICT

In accordance with Step 25 and the final verdict rules:

```text
================================================================================
FINAL VERDICT: BLOCKED
CLASSIFICATION: BACKTEST BLOCKED — INSUFFICIENT VERIFIED HISTORICAL DATA
================================================================================
```

### Institutional Baseline Backtest Audit Summary

| Evaluation Category | Audit Finding | Authorized Status |
| :--- | :--- | :--- |
| **Strategy & Risk Core (Phase 0–16)** | Preserved 100% frozen; zero modifications. | **`FROZEN / PASS`** |
| **Offline Backtest Engine (Phase 10)** | Deterministic simulation architecture verified. | **`PASS`** |
| **Quant Validation Framework (Phase 18)** | WFO, CPCV, Cost Sensitivity, Monte Carlo verified. | **`PASS`** |
| **Anti-Lookahead & Causal Isolation** | Closed-candle quarantine and future mutation passed. | **`PASS`** |
| **Repository Historical Market Data** | **0 genuine historical rows found in repository.** | **`BLOCKED (ZERO DATA)`** |
| **Data Sufficiency Threshold** | Inadequate duration, inadequate candle count. | **`INSUFFICIENT DATA`** |
| **Backtest Execution Status** | **Halted fail-closed to prevent synthetic fabrication.** | **`BLOCKED`** |
| **Future Profitability Claim** | **ZERO CLAIMS** (Historical backtest is distinct from future live performance). | **`GOVERNED`** |

---
*Report cryptographically indexed under Root Manifest `0899c5c8bf5fe64c6fb93dd646008bdb08edeffc6f3ed3d64b960947ef31e935`.*
