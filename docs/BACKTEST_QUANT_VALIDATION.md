# AlphaForge — Phase 10: Backtest & Quant Validation Engine

---

## 1. Phase 10 Purpose & Scope Boundary

Phase 10 introduces the **Deterministic, Research-Grade, Offline-Only Backtest & Quant Validation Engine** (`alphaforge.backtest`), answering the critical quantitative validation questions:
> **"How does AlphaForge empirically evaluate the frozen trading strategy against historical market data without look-ahead bias, without execution leaks, and with mathematically verified derivatives accounting, realistic transaction friction, and rigorous quant validation gates?"**

### Offline-Only Simulation Authority Mandate
The backtest engine is an **offline quantitative validation framework**, strictly isolated from live execution environments. It **MUST NOT**:
- Connect to live brokers, WebSocket feeds, or REST trading endpoints.
- Store, request, or use broker API credentials or secrets.
- Bypass or alter frozen Phase 1–9 domain authorities (Strategy, Risk, Contract, FSM, Cost, Ledger).
- Place live trades or submit orders to production venues (`LIVE_TRADING = False` remains strictly locked).
- Implement future-phase functionality (Phase 11 Replay, Phase 12 Chaos Testing, Phase 15 Deployment, Phase 16 Paper/Shadow, Phase 17 Controlled Live).

---

## 2. Architecture Overview

```text
+-------------------------------------------------------------------------------+
|                            Backtest Dataset Source                            |
|             (BacktestDataset with Canonical SHA-256 Checksum)                 |
+-------------------------------------------------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                         BacktestEngine (Event Loop)                           |
|  - Closed-Candle Isolation (Bar i completed -> evaluated at ts_i)             |
|  - Phase 1: DeterministicStrategyEngine (Signal Generation)                   |
|  - Phase 5: RiskEngine.evaluate_trade_risk (Pre-Trade Limits)                 |
|  - Phase 7: OrderStateMachine (Enforced Lifecycle Transitions)                |
+-------------------------------------------------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                     SimulatedFillEngine (Section 15 Policy)                   |
|  - Conservative Next-Bar Open Execution for Market Entries                    |
|  - Phase 6 CostConfig Integration (Fee + Slippage Calculation)                |
|  - Conservative Same-Bar Ambiguity Policy: SL Triggered Before TP             |
+-------------------------------------------------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                       PortfolioTracker (Decimal Accounting)                   |
|  - Strict Single-Entry Model Invariant (No pyramiding / concurrent entries)   |
|  - Derivatives Identity: Equity == Cash + Margin_Used + Unrealized_PnL        |
|  - Continuous Tick-by-Bar MFE / MAE Excursion Tracking                        |
+-------------------------------------------------------------------------------+
                                       |
         +-----------------------------+-----------------------------+
         |                                                           |
         v                                                           v
+---------------------------------+         +-----------------------------------+
|     Quant Validation Gates      |         |            Audit Ledger           |
|  - Gate A: Look-Ahead Bias      |         |  - Cryptographic Hash Chaining    |
|  - Gate B: Sample Sufficiency   |         |  - Event Types:                   |
|  - Gate C: Overfitting Filter   |         |    BACKTEST_STARTED               |
|  - Gate D: Max Drawdown         |         |    ORDER_SIMULATED                |
|  - Gate E: Sharpe Ratio         |         |    FILL_SIMULATED                 |
|  - Gate F: Realistic Friction   |         |    TRADE_CLOSED                   |
|  - Gate G: Contract Expiry      |         |    BACKTEST_COMPLETED             |
|  - Gate H: Accounting Identity  |         |  - Correlation ID: BT-RUN-<HASH>  |
|  - Gate I: Out-of-Sample Split  |         +-----------------------------------+
|  - Gate J: Regime Robustness    |
+---------------------------------+
         |
         v
+-------------------------------------------------------------------------------+
|                        Forensic Validation Reports                            |
|  - Markdown & ASCII Forensic Formats                                          |
|  - Overfitting Diagnostics (Deflated Sharpe, Concentration, PnL degradation)  |
+-------------------------------------------------------------------------------+
```

---

## 3. Dataset Management & Look-Ahead Bias Prevention

### 3.1 Canonical Dataset Integrity
Datasets are encapsulated within immutable `BacktestDataset` instances. Each dataset generates a canonical SHA-256 checksum over its sorted, validated `MarketCandle` series:
- Every candle is verified to be chronologically sorted (`ts[i] < ts[i+1]`).
- Timeframes and symbols are verified for consistency.
- The canonical hash covers `(timestamp, symbol, open, high, low, close, volume)` represented canonically.

### 3.2 Closed-Candle Execution Isolation
Look-ahead bias is eliminated at the architectural level:
1. **No Intra-Bar Lookahead:** Signals are evaluated strictly upon completed candle close.
2. **Next-Bar Execution:** When a signal is generated at the close of bar $t$, entry orders are queued and executed strictly at the `open` price of bar $t+1$.
3. **No Retroactive Access:** The strategy engine receives only closed historical candles up to bar $t$. Future bars $t+1, \dots, N$ are completely invisible.

---

## 4. Simulated Execution & Section 15 Fill Policy

### 4.1 Friction Integration
All fills incorporate the frozen Phase 6 `CostConfig`:
- **Brokerage & Exchange Fees:** Calculated via frozen percentage-based rates.
- **Execution Slippage:** Simulated against contract tick size and basis points.
- **Deduction Order:** Fees and slippage are deducted in full from portfolio cash and equity upon execution.

### 4.2 Conservative Same-Bar Ambiguity Policy (Section 15)
In historical simulations using bar data (OHLC), the intra-bar sequence between `high` and `low` is unknown. If a bar spans both the resting Stop Loss (SL) price and the resting Take Profit (TP) price:
- **Default Assumption:** Adverse market movement occurs first.
- **Execution Order:** The Stop Loss is executed first at the SL trigger price with slippage. The Take Profit is cancelled.
- This prevents optimistic bias and ensures conservative, stress-tested backtest results.

---

## 5. Portfolio & Derivatives Accounting

### 5.1 Single-Entry Invariant
AlphaForge enforces a strict single-entry model:
- At any point in time, the portfolio maintains either $0$ or $1$ open position.
- Any attempt to open a secondary position while one is open raises `BacktestValidationError`.
- Pyramiding, grid additions, and concurrent conflicting positions are strictly rejected.

### 5.2 Derivatives Accounting Identity
Derivatives contracts (futures) are accounted with explicit initial margin collateral and daily mark-to-market:
$$\text{Equity} = \text{Cash} + \text{Margin Used} + \text{Unrealized PnL}$$
This identity is verified at every bar close snapshot across the entire simulation duration. Any discrepancy exceeding $0.01$ currency units triggers a hard failure under **Gate H**.

### 5.3 Excursion Tracking (MFE & MAE)
- **Maximum Favorable Excursion (MFE):** The maximum favorable price distance (in points) achieved during the trade's lifetime.
- **Maximum Adverse Excursion (MAE):** The maximum adverse price distance (in points) sustained before trade exit.

---

## 6. Quant Validation Gates (Gates A–J)

Every backtest execution is evaluated against 10 deterministic Quant Validation Gates:

| Gate | Name | Rule / Invariant | Threshold | Severity |
| :--- | :--- | :--- | :--- | :--- |
| **Gate A** | Look-Ahead Bias | All execution timestamps $\ge$ signal evaluation timestamps | 0 violations | Hard Fail |
| **Gate B** | Sample Sufficiency | Minimum number of completed trades executed | $\ge 15$ trades | Fail / Warning |
| **Gate C** | Overfitting Filter | Extreme win-rate detection | Win Rate $< 85\%$ | Fail if $>95\%$, Warn if $>85\%$ |
| **Gate D** | Max Drawdown | Maximum portfolio drawdown threshold | Max Drawdown $\le 25\%$ | Fail if $>25\%$ |
| **Gate E** | Sharpe Ratio | Annualized risk-adjusted return hurdle | Sharpe Ratio $\ge 0.5$ | Warning if $<0.5$ |
| **Gate F** | Realistic Friction | Non-zero transaction costs & slippage applied | Fees $+$ Slippage $> 0$ | Hard Fail |
| **Gate G** | Contract Expiry | No trades held past contract expiry datetime | 0 expired trades held | Hard Fail |
| **Gate H** | Accounting Invariants | Identity $\text{Equity} == \text{Cash} + \text{Margin} + \text{Unrealized}$ | 0 identity violations | Hard Fail |
| **Gate I** | Out-of-Sample Support | Deterministic train/test partition & walk-forward support | Verified splits | Hard Fail |
| **Gate J** | Regime Robustness | Evaluates strategy performance across market regimes | Minimum 1 regime evaluated | Warning if unverified |

---

## 7. Audit Ledger Cryptographic Integration

Every backtest run integrates directly with the frozen Phase 9 `AuditLedger`:
- **Deterministic Run Identity:**
  $$\text{BT-RUN-}H_{24}(\text{Config} \parallel \text{Dataset Metadata})$$
- **Correlation ID:** Every audit event emitted by the backtest engine carries `correlation_id == run_id`.
- **Event Types Emitted:**
  - `BACKTEST_STARTED`: Emitted upon simulation start with run configuration and dataset checksum.
  - `ORDER_SIMULATED`: Emitted when an order is generated and queued.
  - `FILL_SIMULATED`: Emitted upon entry or exit fill with effective price, fees, and slippage.
  - `TRADE_CLOSED`: Emitted upon position closure with realized gross and net PnL.
  - `BACKTEST_COMPLETED`: Emitted at test completion with final equity and trade count.
  - `VALIDATION_WARNING`: Emitted when a validation gate raises a warning.
  - `VALIDATION_FAILED`: Emitted when a critical quant gate fails.
- **Tamper-Evident Chain:** The ledger's SHA-256 hash chain is verified via `ledger.verify_chain().valid is True`.

---

## 8. Forensic Reporting & Overfitting Diagnostics

### 8.1 Reporting Formats
- **Markdown Report (`generate_backtest_markdown_report`):** Full report including executive summary, gate matrix, return statistics, drawdown profile, trade analysis, regime breakdown, and audit ledger integrity proof.
- **ASCII Text Report (`generate_backtest_text_report`):** Terminal-friendly tabular summary.

### 8.2 Overfitting Diagnostics
- **Trade Return Concentration:** Verifies that the top 3 trades do not account for more than 70% of total strategy profits.
- **Deflated Sharpe Ratio:** Guards against sample inflation across multi-parameter searches.
- **Walk-Forward In-Sample vs. Out-of-Sample Degradation:** Quantifies PnL degradation across rolling walk-forward folds.

---

## 9. Verification & Safety Posture

- **Total Test Suite:** 488 passing tests across unit, integration, and property-based test suites.
- **Linter & Style:** 100% compliance with `ruff check` and `ruff format`.
- **Type Checking:** 100% compliance with strict `mypy` across all 130 repository source files.
- **Safety Lockdown:** `LIVE_TRADING = False` strictly enforced. Zero network calls, zero broker endpoints, zero credential dependencies.
