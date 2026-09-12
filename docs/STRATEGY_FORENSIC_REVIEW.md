# ALPHAFORGE — STRATEGY FORENSIC SPECIFICATION & REVIEW

**Project Name:** AlphaForge  
**Author:** Principal Quantitative Systems Engineer & Code Auditor  
**Date:** 2026-09-12  
**Status:** Approved Master Strategy Specification  
**Baseline Reference:** AlphaForge Master Constitution (Sections 4, 5, 8, 11)

---

## 1. Quantitative Strategy Mandate & Guiding Philosophy

AlphaForge is designed as an institutional-grade, purely deterministic quantitative trading engine. It operates on the principle of **Zero Profitability Assumptions**:
- No trading strategy is assumed to be profitable a priori.
- Backtests are treated as hypothesis testing tools, not proof of future returns.
- Curve-fitting, cherry-picking, post-hoc indicator parameter tweaking, lookahead bias, and survivorship bias are strictly prohibited.
- The system must prefer **rejecting an uncertain trade** over executing an unsafe trade.

---

## 2. Mandatory Closed-Candle Invariant (Rule 8)

The most catastrophic failure in quantitative systems is lookahead bias caused by reading forming or partially formed candles. AlphaForge enforces the **Closed-Candle Rule** at the architectural level:

```text
[0] = Current Forming Candle (OPEN - STILL MUTATING)  --> STRICTLY PROHIBITED FOR SIGNALS
[1] = Latest Fully Closed Candle                     --> VALID FOR SIGNAL GENERATION
[2] = Previous Fully Closed Candle                    --> VALID FOR SIGNAL GENERATION
...
[N] = Historical Fully Closed Candles                 --> VALID FOR SIGNAL GENERATION
```

### 2.1 Enforcement Mechanism
- The Strategy Signal Engine receives only a slice of candles ending at index `[1]`.
- Index `[0]` is quarantined within the Data Ingestion Layer and never provided to the `evaluate_signals()` method.
- Any attempt by an indicator or rule to inspect `[0]` triggers an immediate architectural assertion error and halts the engine (`REJECT_CLOSED_CANDLE_VIOLATION`).

---

## 3. V1 Strategy Architecture

AlphaForge V1 implements a **Single Deterministic Strategy** with strict multi-timeframe and index-futures confirmation.

### 3.1 Timeframe Hierarchy
1. **Primary Execution Timeframe ($TF_{exec}$):** Lower timeframe (e.g., 3-minute or 5-minute fully closed candles) used for pattern trigger, precise entry, stop-loss, and target calculation.
2. **Confirmation Timeframe ($TF_{conf}$):** Higher timeframe (e.g., 15-minute fully closed candles) used for directional trend bias, volatility regime detection, and structural support/resistance.

### 3.2 Dual-Instrument Confirmation (Index + Futures)
The strategy analyzes both the underlying cash index (e.g., NIFTY / BANKNIFTY spot) and the active front-month futures contract:
- **Cash Index:** Free from rollover premiums and expiration distortion, provides pure institutional reference levels.
- **Active Futures Contract:** Provides real volume, open interest dynamics, and basis behavior.
- **Confirmation Requirement:** An entry signal is valid **ONLY IF** both the Cash Index and the Futures Contract confirm the setup directionally simultaneously on candle `[1]`.

### 3.3 Strict Single-Leg Trade Structure
To eliminate execution complexity and slippage hazards in V1:
- **Single Entry:** 100% of the calculated lot quantity is entered at once upon confirmed signal. No scaling in.
- **Single Stop-Loss:** Calculated deterministically from market structure (e.g., swing high/low of closed candle or ATR multiplier). Placed immediately upon fill.
- **Single Profit Target:** Fixed risk-reward target (e.g., $1:2$ or $1:2.5$ R:R). No subjective trailing stops or partial exits in V1.
- **No Averaging Down:** Strictly forbidden. If price moves against the position, it must exit at the hard stop-loss.

---

## 4. Formal Signal Rejection Catalog

Every tick or candle cycle must evaluate deterministic rules. If any rule fails, the strategy does **not** silently drop the signal; instead, it emits an explicit `SignalRejection` event with a codified reason:

| Rejection Code | Description | Severity |
| :--- | :--- | :--- |
| `REJECT_CLOSED_CANDLE_VIOLATION` | Attempted access to forming candle `[0]`. | P0 (Bug) |
| `REJECT_DATA_STALE` | Timestamp of latest closed candle exceeds max allowed delay. | P1 |
| `REJECT_DATA_GAP` | Missing candles detected in historical sequence. | P0 |
| `REJECT_HTF_NONCONFIRMATION` | Higher timeframe trend contradicts lower timeframe signal. | Normal Gate |
| `REJECT_FUTURES_NONCONFIRMATION`| Futures price action fails to confirm Cash Index direction. | Normal Gate |
| `REJECT_INDEX_FUTURES_DESYNC` | Timestamps between Index and Futures data feeds differ > threshold. | P1 |
| `REJECT_BASIS_ABNORMAL` | Index-Futures basis z-score exceeds statistical tolerance. | P1 |
| `REJECT_INVALID_CONTRACT` | Contract is expired, illiquid, or within rollover blackout. | P1 |
| `REJECT_SPREAD_TOO_WIDE` | Bid-Ask spread exceeds maximum allowable transaction cost threshold. | Normal Gate |
| `REJECT_VOLATILITY_OUT_OF_BOUNDS`| ATR is below minimum threshold (flat) or above circuit breaker limit. | Normal Gate |
| `REJECT_SESSION_TIME_RESTRICTED` | Signal generated outside permitted trading window (e.g. market open/close). | Normal Gate |

---

## 5. Explicitly Excluded Features (Master Specification Section 5)

The following capabilities are **explicitly excluded from V1** and will not be implemented:
1. **NO AI / Deep Learning Signal Generation:** Eliminates black-box non-determinism.
2. **NO ML Scoring Models:** No random forests, XGBoost, or dynamic weighting.
3. **NO News / Sentiment Analysis:** No Twitter/X feeds, news scrapers, or LLM sentiment processors.
4. **NO Averaging Down or Martingale Sizing:** No increasing position size during adverse excursion.
5. **NO Automatic Parameter Optimization:** No online or continuous hyperparameter tuning in live environments.
6. **NO Uncontrolled Strategy Switching:** The single deterministic strategy cannot be swapped dynamically.
7. **NO Multi-Stage Exits:** No complex discretionary laddering or partial profit takes in V1.
