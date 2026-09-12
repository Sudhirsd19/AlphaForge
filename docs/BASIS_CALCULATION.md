# AlphaForge ? Phase 4: Basis Calculation & Statistical Model

---

## 1. Mathematical Formulas & Precision Specifications

All basis calculations in AlphaForge are implemented using fixed-point `Decimal` arithmetic. Standard floating-point types (`float`) are strictly forbidden to eliminate binary precision degradation and platform-dependent rounding variance.

### 1.1 Absolute Basis
The absolute basis $B_t$ measures the raw price premium or discount of the derivative contract relative to its underlying spot index:

$$B_t = F_t - S_t$$

Where:
- $F_t$: Futures contract reference price (candle close) at time $t$
- $S_t$: Spot / Index reference price (candle close) at time $t$

When $B_t > 0$, the futures contract trades at a premium (contango).
When $B_t < 0$, the futures contract trades at a discount (backwardation).

#### Fail-Closed Policy on Defective Observations
If an observation is defective or invalid (`basis_status != BasisStatus.VALID`), $B_t$ is strictly `None`. It is never reported as `Decimal("0")` merely because calculation failed, ensuring invalid observations never appear numerically valid.

### 1.2 Normalized Basis Percentage
Because raw index points vary over time and across underlying instruments, the normalized basis percentage $b_t$ scales the absolute basis by the spot index price:

$$b_t = \frac{B_t}{S_t} = \frac{F_t - S_t}{S_t}$$

#### Fail-Closed Zero-Division Policy
If $S_t \le 0$ (non-positive index reference price), `calculate_basis_pct()` immediately raises `BasisCalculationError`. In the engine pipeline, non-positive or non-finite prices evaluate to `BasisStatus.INVALID` with price and basis fields set to `None`. No placeholder prices (such as `1`) are ever generated.

### 1.3 Timestamp Skew
The timestamp misalignment between spot index and futures candles is measured in absolute seconds:

$$\Delta t = |\tau_F - \tau_S|$$

Where $\tau_F$ and $\tau_S$ are the UTC timestamps of the respective candle closes. If $\Delta t > \text{max\_timestamp\_skew\_seconds}$ (default 60 seconds), the observation evaluates to `BasisStatus.MISALIGNED_TIMESTAMP`.

---

## 2. Rolling Statistical Window (Window Size = 20)

To establish dynamic regime context, the basis engine tracks a rolling historical window of the normalized basis percentage:

$$\mathcal{H} = [b_{t - K + 1}, \dots, b_{t - 1}, b_t]$$

Where:
- Configured window size $K = 20$.
- Observations are ordered chronologically.
- If $|\mathcal{H}| > 20$, only the most recent 20 observations are used.

### 2.1 Rolling Mean ($\mu_t$)
The arithmetic mean of the rolling window:

$$\mu_t = \frac{1}{K} \sum_{i=1}^{K} b_i$$

### 2.2 Rolling Sample Standard Deviation ($\sigma_t$) with Bessel's Correction
To correct for downward sample bias in finite sample variance, Bessel's correction with degrees of freedom $K - 1 = 19$ is enforced:

$$s^2_t = \frac{1}{K - 1} \sum_{i=1}^{K} (b_i - \mu_t)^2$$

$$\sigma_t = \sqrt{s^2_t}$$

In `calculate_rolling_stats()`, $\sqrt{s^2_t}$ is evaluated directly on `Decimal` via the native `Decimal.sqrt()` function, ensuring exact precision without intermediate conversion to float.

---

## 3. Deterministic Z-Score & Regime Thresholds

The standardized basis deviation (Z-score) measures how many standard deviations the current basis percentage deviates from its recent historical mean:

$$Z_t = \frac{b_t - \mu_t}{\sigma_t}$$

### 3.1 Regime Classification & Confirmation Mapping

Thresholds defined in `BasisConfig` are authoritative:

| Z-Score Range | `BasisZScoreStatus` | Confirmation Status | Regime Description | Strategy Interpretation |
| :--- | :--- | :--- | :--- | :--- |
| $Z_t < \text{lower\_threshold}$ | `LOWER` | `NOT_CONFIRMED` | Extreme Basis Compression / Backwardation | Severe discount; potential breakdown or rollover anomaly |
| $\text{lower\_threshold} \le Z_t \le \text{upper\_threshold}$ | `NORMAL` | `CONFIRMED` | Stable Basis Corridor (Inclusive) | Equilibrium pricing; valid for trend/breakout trade execution |
| $Z_t > \text{upper\_threshold}$ | `HIGHER` | `NOT_CONFIRMED` | Extreme Basis Expansion / Contango | Severe premium; potential exuberance or market dislocation |
| Undefined / $N < 20$ / $\sigma = 0$ | `UNDEFINED` | `NOT_CONFIRMED` | Statistical Indeterminacy | Insufficient data or zero variance |

#### Boundary Inclusivity
The boundaries for `NORMAL` are strictly inclusive:
- If $Z_t = \text{z\_score\_lower\_threshold}$, status is `NORMAL` $\rightarrow$ `CONFIRMED`.
- If $Z_t = \text{z\_score\_upper\_threshold}$, status is `NORMAL` $\rightarrow$ `CONFIRMED`.
- Custom configured thresholds (e.g. `[-1.0, 1.0]`) are authoritative.

### 3.2 Edge Cases and Fail-Closed Protections

1. **Insufficient History ($N < 20$):**
   - If the observation history contains fewer than 20 entries, statistical inference is rejected:
     - `rolling_mean = None`
     - `rolling_std = None`
     - `z_score = None`
     - `z_score_status = BasisZScoreStatus.UNDEFINED`
     - `confirmation_status = BasisConfirmationStatus.NOT_CONFIRMED`
2. **Zero Standard Deviation ($\sigma_t = 0$):**
   - If all 20 observations in the window are identical, sample standard deviation is 0.
   - To prevent division by zero, `z_score` is set to `None`, `z_score_status` evaluates to `BasisZScoreStatus.UNDEFINED`, and confirmation status is `NOT_CONFIRMED`.
3. **Non-Finite Arithmetic Protection:**
   - Any input containing `NaN`, `sNaN`, or `Infinity` is rejected before calculation begins.
