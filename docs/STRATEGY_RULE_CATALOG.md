# ALPHAFORGE — FORMAL STRATEGY RULE CATALOG

**Project Name:** AlphaForge  
**Document Type:** Formal Mathematical Strategy Rule Catalog  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Author:** Principal Quantitative Systems Engineer  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan & Strategy Specification

---

## 1. Rule Catalog Schema Specification

Every strategy rule is codified under the rigorous institutional schema:
- **Rule ID:** Stable identifier (`RULE-AF-xxx`).
- **Purpose:** Architectural or quantitative intent.
- **Inputs:** Required data series and parameters.
- **Timeframe:** Evaluation timeframe ($TF_{exec}$ or $TF_{conf}$).
- **Lookback:** Minimum closed candle lookback count required.
- **Formula:** Exact mathematical formulation.
- **Threshold:** Numerical criteria for pass/fail.
- **Evaluation Timing:** Exact moment of evaluation.
- **Required Data Quality:** Input prerequisites (`VALIDATED`, non-stale).
- **Invalidation:** Condition that negates the evaluation.
- **Fallback:** Default behavior upon missing/ambiguous inputs.
- **Output:** Typed result or state emitted.
- **Logging Fields:** Structured audit metadata.
- **Rejection Code:** Codified error/rejection enum emitted on failure.

---

## 2. Formal Strategy Rules

### RULE-AF-001: Closed-Candle Quarantine Rule
- **Rule ID:** `RULE-AF-001`
- **Purpose:** Prevent lookahead bias by strictly isolating the mutating forming candle `[0]`.
- **Inputs:** Raw candle array $S = [C_0, C_1, \dots, C_N]$.
- **Timeframe:** All timeframes ($TF_{exec}, TF_{conf}$).
- **Lookback:** $N \ge 2$.
- **Formula:** $S_{eval} = S[1:]$. All downstream rules evaluate strictly on $S_{eval}$.
- **Threshold:** Index 0 must never be accessed by feature or strategy engines.
- **Evaluation Timing:** Pre-processing gate prior to indicator computation.
- **Required Data Quality:** $C_1$ status must be closed (`is_closed == True`).
- **Invalidation:** If input array contains fewer than 2 candles.
- **Fallback:** Fail closed; abort evaluation.
- **Output:** Validated closed candle slice $S_{eval}$.
- **Logging Fields:** `total_candles_received`, `quarantined_index_0_timestamp`.
- **Rejection Code:** `REJECT_CLOSED_CANDLE_VIOLATION`.

---

### RULE-AF-002: Data Freshness & Stale Feed Guard
- **Rule ID:** `RULE-AF-002`
- **Purpose:** Ensure latest closed candle $C_1$ is not stale relative to evaluation context.
- **Inputs:** Exchange timestamp of $C_1$, evaluation timestamp $T_{eval}$, max allowed age $T_{max\_age}$.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 1 candle.
- **Formula:** $\Delta T = T_{eval} - \text{timestamp}(C_1)$.
- **Threshold:** $\Delta T \le \text{TimeframeInterval} + 15\text{ seconds}$.
- **Evaluation Timing:** Pre-processing gate.
- **Required Data Quality:** Exchange timestamps monotonically increasing.
- **Invalidation:** Clock desync or disconnected feed.
- **Fallback:** Reject trade.
- **Output:** Boolean `is_fresh`.
- **Logging Fields:** `delta_t_seconds`, `max_allowed_age_seconds`.
- **Rejection Code:** `REJECT_DATA_STALE`.

---

### RULE-AF-003: Higher-Timeframe Trend Regime Filter
- **Rule ID:** `RULE-AF-003`
- **Purpose:** Establish directional market regime on confirmation timeframe.
- **Inputs:** Closed higher-timeframe candles $S_{conf}$, $EMA_9$, $EMA_{21}$.
- **Timeframe:** $TF_{conf}$ (e.g. 15-minute).
- **Lookback:** 21 closed candles on $TF_{conf}$.
- **Formula:**
  - $\text{Bullish} \iff (EMA_9(C_1) > EMA_{21}(C_1)) \land (Close(C_1) > EMA_{21}(C_1))$
  - $\text{Bearish} \iff (EMA_9(C_1) < EMA_{21}(C_1)) \land (Close(C_1) < EMA_{21}(C_1))$
- **Threshold:** Strict inequality; no rounding.
- **Evaluation Timing:** On receipt of closed $TF_{exec}$ candle, synchronized with latest closed $TF_{conf}$ candle.
- **Required Data Quality:** 21 continuous closed $TF_{conf}$ bars without data gaps.
- **Invalidation:** $EMA_9$ and $EMA_{21}$ crossover within candle $C_1$.
- **Fallback:** Return `TrendState.NEUTRAL`.
- **Output:** `TrendState.BULLISH` | `TrendState.BEARISH` | `TrendState.NEUTRAL`.
- **Logging Fields:** `htf_ema9`, `htf_ema21`, `htf_close`, `trend_state`.
- **Rejection Code:** `REJECT_TREND`.

---

### RULE-AF-004: 20-Period Range & Breakout Detection
- **Rule ID:** `RULE-AF-004`
- **Purpose:** Identify range expansion beyond the 20-period swing boundary.
- **Inputs:** Closed candles $S_{exec}[1..21]$.
- **Timeframe:** $TF_{exec}$ (e.g. 3m or 5m).
- **Lookback:** 21 closed candles ($C_1$ trigger candle + 20 prior reference candles).
- **Formula:**
  - $R_{20} = \max_{k \in [2, 21]} High(C_k)$
  - $S_{20} = \min_{k \in [2, 21]} Low(C_k)$
  - $\text{Long Breakout} \iff Close(C_1) > R_{20}$
  - $\text{Short Breakout} \iff Close(C_1) < S_{20}$
- **Threshold:** Strict price breakout ($Close > R_{20}$ or $Close < S_{20}$).
- **Evaluation Timing:** At close of candle $C_1$.
- **Required Data Quality:** 21 consecutive valid closed bars on $TF_{exec}$.
- **Invalidation:** Price re-enters range before bar close.
- **Fallback:** Reject trade.
- **Output:** Boolean `breakout_long`, `breakout_short`.
- **Logging Fields:** `resistance_20`, `support_20`, `trigger_close`.
- **Rejection Code:** `REJECT_BREAKOUT`.

---

### RULE-AF-005: Confirmation Candle Geometry Filter
- **Rule ID:** `RULE-AF-005`
- **Purpose:** Filter out weak, indecisive, or exhausted breakout bars (e.g. dojis or long opposing wicks).
- **Inputs:** Trigger candle $C_1$ ($Open, High, Low, Close$).
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 1 candle ($C_1$).
- **Formula:**
  - $\text{Range} = High(C_1) - Low(C_1)$
  - $\text{Body} = |Close(C_1) - Open(C_1)|$
  - $\text{BodyRatio} = \frac{\text{Body}}{\text{Range}}$
  - $\text{CloseRatio}_{long} = \frac{Close(C_1) - Low(C_1)}{\text{Range}}$
  - $\text{CloseRatio}_{short} = \frac{High(C_1) - Close(C_1)}{\text{Range}}$
- **Threshold:** $\text{BodyRatio} \ge 0.50$ AND ($\text{CloseRatio}_{long} \ge 0.70$ for LONG or $\text{CloseRatio}_{short} \ge 0.70$ for SHORT).
- **Evaluation Timing:** At close of candle $C_1$.
- **Required Data Quality:** $\text{Range} > 0$ (non-zero range).
- **Invalidation:** Zero range candle.
- **Fallback:** Reject trade.
- **Output:** Boolean `geometry_valid`.
- **Logging Fields:** `body_ratio`, `close_ratio`.
- **Rejection Code:** `REJECT_CANDLE_GEOMETRY`.

---

### RULE-AF-006: Relative Volume Spike Filter
- **Rule ID:** `RULE-AF-006`
- **Purpose:** Confirm that the breakout is accompanied by institutional liquidity expansion.
- **Inputs:** Volume of $C_1$ and volumes of $C_2 \dots C_{21}$.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 21 closed candles.
- **Formula:** $V_{ratio} = \frac{Volume(C_1)}{\frac{1}{20} \sum_{k=2}^{21} Volume(C_k)}$.
- **Threshold:** $V_{ratio} \ge 1.20$.
- **Evaluation Timing:** At close of candle $C_1$.
- **Required Data Quality:** Valid non-negative volume on all 21 bars.
- **Invalidation:** Volume average equals 0.
- **Fallback:** Reject trade.
- **Output:** Boolean `volume_confirmed`.
- **Logging Fields:** `candle_volume`, `volume_ma20`, `volume_ratio`.
- **Rejection Code:** `REJECT_VOLUME`.

---

### RULE-AF-007: Momentum Filter (14-Period RSI)
- **Rule ID:** `RULE-AF-007`
- **Purpose:** Verify directional velocity without buying into exhausted overbought/oversold extremes.
- **Inputs:** Closed prices $S_{exec}[1..15]$.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 15 closed candles (Wilder's 14-period RSI).
- **Formula:** Standard 14-period Wilder RSI formula.
- **Threshold:**
  - For LONG: $50.0 < RSI(C_1) \le 75.0$.
  - For SHORT: $25.0 \le RSI(C_1) < 50.0$.
- **Evaluation Timing:** At close of candle $C_1$.
- **Required Data Quality:** 15 continuous closed bars.
- **Invalidation:** Undefined RSI (zero price variation across 14 bars).
- **Fallback:** Reject trade.
- **Output:** Boolean `momentum_confirmed`.
- **Logging Fields:** `rsi_value`.
- **Rejection Code:** `REJECT_MOMENTUM`.

---

### RULE-AF-008: Volatility Regime Filter (14-Period ATR)
- **Rule ID:** `RULE-AF-008`
- **Purpose:** Ensure market is neither dead (untradable noise) nor in extreme abnormal volatility spike.
- **Inputs:** Closed candles $S_{exec}[1..15]$.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 15 closed candles.
- **Formula:** $ATR_{14}(C_1) = \text{WilderATR}(High, Low, Close, 14)$.
- **Threshold:**
  - Minimum: $ATR_{14} \ge 0.05\% \times Close(C_1)$
  - Maximum: $ATR_{14} \le 1.50\% \times Close(C_1)$
- **Evaluation Timing:** At close of candle $C_1$.
- **Required Data Quality:** 15 continuous closed bars.
- **Invalidation:** ATR $\le 0$.
- **Fallback:** Reject trade.
- **Output:** Boolean `volatility_valid`.
- **Logging Fields:** `atr_14`, `atr_pct_of_price`.
- **Rejection Code:** `REJECT_VOLATILITY`.

---

### RULE-AF-009: Futures Confirmation Interface Gate
- **Rule ID:** `RULE-AF-009`
- **Purpose:** Enforce institutional futures confirmation before allowing cash index signals to proceed.
- **Inputs:** Consumed `FuturesConfirmationStatus`.
- **Timeframe:** Synchronous with $TF_{exec}$.
- **Lookback:** N/A (Interface consumer).
- **Formula:** `status == FuturesConfirmationStatus.CONFIRMED`.
- **Threshold:** Exactly `CONFIRMED`.
- **Evaluation Timing:** Real-time during signal evaluation.
- **Required Data Quality:** Valid futures feed status packet.
- **Invalidation:** `status \in {NOT_CONFIRMED, INVALID, STALE, UNAVAILABLE}`.
- **Fallback:** Reject trade.
- **Output:** Boolean `futures_confirmed`.
- **Logging Fields:** `futures_status`.
- **Rejection Code:** `REJECT_FUTURES_CONFIRMATION`.

---

### RULE-AF-010: Structural Stop-Loss Calculation & Boundary Gate
- **Rule ID:** `RULE-AF-010`
- **Purpose:** Deterministically establish initial protective stop price and verify minimum/maximum distance bounds.
- **Inputs:** $C_1 \dots C_K$ ($K = \text{swing\_stop\_lookback}$, default $K=2$), $ATR_{14}$, Direction.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** $K = \text{swing\_stop\_lookback}$ closed candles (default: 2) + ATR.
- **Formula:**
  - $\text{Long Stop: } P_{stop} = \min_{i \in [1..K]}(Low(C_i)) - 1.0 \times ATR_{14}$
  - $\text{Short Stop: } P_{stop} = \max_{i \in [1..K]}(High(C_i)) + 1.0 \times ATR_{14}$
  - $\text{RiskDistance} = |Close(C_1) - P_{stop}|$
- **Threshold:** $0.10\% \times Close(C_1) \le \text{RiskDistance} \le 3.0\% \times Close(C_1)$ AND $P_{stop} > 0$.
- **Evaluation Timing:** At signal generation.
- **Required Data Quality:** Valid positive prices.
- **Invalidation:** $\text{RiskDistance}$ violates boundary thresholds.
- **Fallback:** Reject trade.
- **Output:** Decimal $P_{stop}$, Decimal $\text{RiskDistance}$.
- **Logging Fields:** `stop_price`, `risk_distance`, `risk_distance_pct`, `swing_stop_lookback`.
- **Rejection Code:** `REJECT_INVALID_STOP`.

---

### RULE-AF-011: Fixed 1:2 Reward-to-Risk Target Calculation
- **Rule ID:** `RULE-AF-011`
- **Purpose:** Deterministically calculate single profit target.
- **Inputs:** $P_{entry} = Close(C_1)$, $P_{stop}$, $\text{RiskDistance}$, Direction.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** N/A.
- **Formula:**
  - $\text{Long Target: } P_{target} = P_{entry} + 2.0 \times \text{RiskDistance}$
  - $\text{Short Target: } P_{target} = P_{entry} - 2.0 \times \text{RiskDistance}$
- **Threshold:** $P_{target} > 0$ AND $P_{target} > P_{entry}$ (Long) or $P_{target} < P_{entry}$ (Short).
- **Evaluation Timing:** At signal generation.
- **Required Data Quality:** Valid positive entry and stop prices.
- **Invalidation:** $P_{target} \le 0$.
- **Fallback:** Reject trade.
- **Output:** Decimal $P_{target}$.
- **Logging Fields:** `target_price`, `reward_to_risk_ratio`.
- **Rejection Code:** `REJECT_INVALID_TARGET`.

---

### RULE-AF-012: Deterministic Signal Expiry & Deduplication
- **Rule ID:** `RULE-AF-012`
- **Purpose:** Prevent stale signal execution and duplicate orders on identical setup bars.
- **Inputs:** $C_1$ timestamp, current evaluation timestamp, prior emitted signal history.
- **Timeframe:** $TF_{exec}$.
- **Lookback:** 1 candle.
- **Formula:**
  - $\text{Expiry: } T_{eval} > \text{timestamp}(C_1) + \text{TimeframeDuration}$
  - $\text{Duplicate: } \text{signal\_id} \in \text{EmittedSignalSet}$
- **Threshold:** Exactly 1 trade setup allowed per closed bar.
- **Evaluation Timing:** At decision dispatch.
- **Required Data Quality:** Valid UTC timestamps.
- **Invalidation:** Elapsed timeframe window.
- **Fallback:** Emit `EXPIRED` or `DUPLICATE` decision.
- **Output:** `StrategyDecision.EXPIRED` | `StrategyDecision.DUPLICATE`.
- **Logging Fields:** `signal_id`, `expiry_timestamp`.
- **Rejection Code:** `REJECT_EXPIRED` | `REJECT_DUPLICATE`.
