# ALPHAFORGE — STRATEGY SPECIFICATION (V1 DETERMINISTIC CORE)

**Project Name:** AlphaForge  
**Document Type:** Formal Strategy Specification  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Author:** Principal Quantitative Systems Engineer  
**Date:** 2026-09-12  
**Status:** APPROVED STRATEGY SPECIFICATION  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan & Phase 0 Requirement Freeze

---

## 1. Quantitative Core Philosophy & Determinism

AlphaForge V1 implements a single, institutionally robust, deterministic trend-breakout-confirmation strategy designated:
$$\mathbf{AF\_ORB\_MOMENTUM\_V1}$$

The strategy operates as an immutable mathematical function:
$$\mathcal{S}: (S_{exec}, S_{conf}, C_{fut}, \Theta_{cfg}, T_{eval}) \longrightarrow \Sigma$$
Where:
- $S_{exec}$: Ordered sequence of candles on the primary execution timeframe ($TF_{exec}$, default: 3-minute or 5-minute).
- $S_{conf}$: Synchronized sequence of candles on the higher confirmation timeframe ($TF_{conf}$, default: 15-minute).
- $C_{fut}$: Status contract of the active futures contract (`FuturesConfirmationStatus`).
- $\Theta_{cfg}$: Canonical immutable configuration parameters.
- $T_{eval}$: Current evaluation timestamp (derived from market feed, zero wall-clock dependency).
- $\Sigma$: Resultant immutable `StrategySignal`.

---

## 2. Mandatory Closed-Candle Contract (Rule 8)

The indexing model of candle arrays is defined as:
```text
Index [0] = Current Forming Candle (OPEN - LIVE TICKS)   ==> QUARANTINED (Zero Signal Use)
Index [1] = Latest Fully Closed Candle                  ==> EVALUATION TRIGGER & GEOMETRY
Index [2] = Previous Fully Closed Candle                 ==> STRUCTURAL REFERENCE
Index [3] = Historical Fully Closed Candle               ==> INDICATOR LOOKBACK
...
Index [N] = Oldest Historical Candle                    ==> INDICATOR LOOKBACK
```

### 2.1 Architectural Isolation Mechanism
The strategy engine accepts candle sequences. Before any indicator or rule computation commences, the engine strips index `[0]`:
$$S_{eval} = S[1 : N]$$
Any attempt to evaluate indicators, calculate support/resistance, or check triggers using index `[0]` raises a hard architectural exception. Property-based tests guarantee that mutating high, low, close, or volume of candle `[0]` produces zero change in the emitted `StrategySignal`.

---

## 3. Multi-Timeframe Synchronization Contract

The strategy evaluates two synchronized timeframes:
1. **Primary Execution Timeframe ($TF_{exec}$):** Lower timeframe (e.g. 3-minute or 5-minute) used for setup trigger, candle geometry, breakout verification, stop loss, and profit target calculations.
2. **Confirmation Timeframe ($TF_{conf}$):** Higher timeframe (e.g. 15-minute) used for trend regime bias.

### 3.1 Higher-Timeframe Candle Eligibility Rule
A candle $C_{conf} \in S_{conf}$ is eligible for higher-timeframe trend evaluation **if and only if**:
$$\text{close\_timestamp}(C_{conf}) \le \text{close\_timestamp}(S_{exec}[1])$$
A higher-timeframe candle that is still forming during the execution candle is strictly ineligible. The latest fully closed higher-timeframe candle satisfying this condition is denoted $C_{conf}[1]$.

---

## 4. Formal Strategy Components

### 4.1 Trend Regime Filter ($TF_{conf}$)
Trend is evaluated on $S_{conf}$ using Exponential Moving Averages ($EMA_{fast} = 9$, $EMA_{slow} = 21$):
- **BULLISH:**
  $$EMA_{fast}(C_{conf}[1]) > EMA_{slow}(C_{conf}[1]) \quad \land \quad Close(C_{conf}[1]) > EMA_{slow}(C_{conf}[1])$$
- **BEARISH:**
  $$EMA_{fast}(C_{conf}[1]) < EMA_{slow}(C_{conf}[1]) \quad \land \quad Close(C_{conf}[1]) < EMA_{slow}(C_{conf}[1])$$
- **NEUTRAL:**
  All other states (divergent EMAs or price chopped between EMAs). In NEUTRAL regime, all entries are rejected (`REJECT_TREND`).

### 4.2 Support / Resistance Construction ($TF_{exec}$)
Dynamic structural levels are determined over a 20-period lookback of closed candles:
- **Resistance Level ($R_{20}$):**
  $$R_{20} = \max_{k \in [2, 21]} High(S_{exec}[k])$$
- **Support Level ($S_{20}$):**
  $$S_{20} = \min_{k \in [2, 21]} Low(S_{exec}[k])$$
Note: The latest closed candle `[1]` is excluded from the lookback window so that `[1]` can be evaluated as breaking out of the established prior range.

### 4.3 Breakout Condition ($TF_{exec}$)
- **LONG Breakout:**
  $$Close(S_{exec}[1]) > R_{20}$$
- **SHORT Breakout:**
  $$Close(S_{exec}[1]) < S_{20}$$

### 4.4 Confirmation Candle Geometry ($TF_{exec}$)
Candle `[1]` must demonstrate definitive institutional commitment without excessive indecision wicks:
1. **Minimum Body Ratio:**
   $$\text{Range}_{[1]} = High(S_{exec}[1]) - Low(S_{exec}[1])$$
   $$\text{Body}_{[1]} = |Close(S_{exec}[1]) - Open(S_{exec}[1])|$$
   $$\frac{\text{Body}_{[1]}}{\text{Range}_{[1]}} \ge 0.50$$
2. **Close Location Ratio:**
   - For LONG:
     $$\frac{Close(S_{exec}[1]) - Low(S_{exec}[1])}{\text{Range}_{[1]}} \ge 0.70 \quad (\text{Close in top 30\% of range})$$
   - For SHORT:
     $$\frac{High(S_{exec}[1]) - Close(S_{exec}[1])}{\text{Range}_{[1]}} \ge 0.70 \quad (\text{Close in bottom 30\% of range})$$

### 4.5 Momentum Filter ($TF_{exec}$)
Calculated using 14-period Relative Strength Index (RSI) on closed candles:
- **For LONG:**
  $$50.0 < RSI(S_{exec}[1]) \le 75.0$$
  *(Momentum is positive, not extreme overbought).*
- **For SHORT:**
  $$25.0 \le RSI(S_{exec}[1]) < 50.0$$
  *(Momentum is negative, not extreme oversold).*

### 4.6 Relative Volume Filter ($TF_{exec}$)
Breakout must be supported by liquidity expansion:
$$V_{ratio} = \frac{Volume(S_{exec}[1])}{\text{SMA}(Volume, 20)_{[2..21]}} \ge 1.20$$
*(Volume is at least 120% of the 20-period moving average volume).*

### 4.7 Futures Confirmation Interface
The strategy consumes the external futures confirmation gate:
- Must be strictly `FuturesConfirmationStatus.CONFIRMED`.
- Any other state (`NOT_CONFIRMED`, `INVALID`, `STALE`, `UNAVAILABLE`) immediately rejects the signal with `REJECT_FUTURES_CONFIRMATION`.

---

## 5. Entry, Stop-Loss & Target Specifications

### 5.1 Entry Reference Price
- The entry reference price is the exact close of the trigger candle `[1]`:
  $$P_{entry} = Close(S_{exec}[1])$$

### 5.2 Stop-Loss Reference Price
The initial protective stop-loss is placed beyond structural support/resistance over `swing_stop_lookback` closed bars (default $K=2$) with an Average True Range ($ATR_{14}$) buffer:
- **For LONG:**
  $$P_{stop} = \min_{k \in [1..K]}(Low(S_{exec}[k])) - 1.0 \times ATR_{14}(S_{exec}[1])$$
- **For SHORT:**
  $$P_{stop} = \max_{k \in [1..K]}(High(S_{exec}[k])) + 1.0 \times ATR_{14}(S_{exec}[1])$$
*(For default $K=2$, this evaluates to $\min(Low[1], Low[2]) - 1.0 \times ATR$ and $\max(High[1], High[2]) + 1.0 \times ATR$ respectively).*

### 5.3 Risk Distance & Validity
$$\text{RiskDistance} = |P_{entry} - P_{stop}|$$
- **Minimum Distance:** $\text{RiskDistance} \ge 0.10\% \times P_{entry}$ (prevents sub-tick noise stops).
- **Maximum Distance:** $\text{RiskDistance} \le 3.0\% \times P_{entry}$ (prevents excessive risk per trade).
- If $\text{RiskDistance}$ fails these bounds, signal is rejected with `REJECT_INVALID_STOP`.

### 5.4 Target Reference Price (Fixed 1:2 R:R)
In accordance with V1 single-target scope:
- **For LONG:**
  $$P_{target} = P_{entry} + 2.0 \times \text{RiskDistance}$$
- **For SHORT:**
  $$P_{target} = P_{entry} - 2.0 \times \text{RiskDistance}$$

---

## 6. Signal Lifecycle: Expiry, Invalidation & Duplication

1. **Signal Expiry:**
   A signal is valid only for the duration of the candle immediately following trigger candle `[1]`. If execution is not triggered within 1 timeframe period of `signal_timestamp`, the signal transitions to `EXPIRED`.
2. **Duplicate Detection:**
   Signals evaluated on the same `(strategy_id, symbol, timeframe, signal_timestamp)` are flagged as `DUPLICATE` to prevent multiple entries on the same bar.
3. **Invalidation:**
   If price crosses $P_{stop}$ prior to execution, the setup is immediately marked invalidated.
