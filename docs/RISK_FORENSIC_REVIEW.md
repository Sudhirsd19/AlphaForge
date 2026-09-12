# ALPHAFORGE — RISK FORENSIC SPECIFICATION & RISK INVARIANTS

**Project Name:** AlphaForge  
**Author:** Chief Risk Officer, Senior Quantitative Systems Engineer  
**Date:** 2026-09-12  
**Status:** Approved Risk Engine Specification  
**Baseline Reference:** AlphaForge Master Constitution (Sections 7 & 12)

---

## 1. Absolute Independence of Risk Controls

The Risk Engine in AlphaForge is an **autonomous, unbypassable gatekeeper**. 
- It exists outside the Strategy Engine and execution paths.
- The Strategy proposes trade intent (`SignalIntent`); the Risk Engine holds unilateral veto power.
- Under no circumstances can a strategy configuration, user setting, or broker override bypass risk invariants.

---

## 2. Hard Risk Invariants

The Risk Engine enforces non-negotiable mathematical and operational invariants:

| # | Risk Invariant | Mathematical / Operational Rule | Failure Action |
| :- | :--- | :--- | :--- |
| 1 | **Max Daily Loss Limit** | $\sum \text{Realized PnL} + \sum \text{Unrealized PnL} \le -L_{max}$ | Immediate trading lockout for remainder of session (`LOCKOUT_DAILY_LOSS_BREACH`). |
| 2 | **Max Open Risk Ceiling** | $\sum_{i \in \text{OpenPositions}} (\text{Entry}_i - \text{Stop}_i) \times \text{Qty}_i \le R_{open\_max}$ | Reject incoming signal (`REJECT_MAX_OPEN_RISK_EXCEEDED`). |
| 3 | **Valid Stop-Loss Distance** | $\text{StopDistance} = |\text{Entry} - \text{Stop}| \ge \text{MinDistance}$ and $\text{Stop} > 0$ | Reject invalid stop (`REJECT_INVALID_STOP_DISTANCE`). |
| 4 | **No Martingale / No Averaging Down** | $\text{Qty}_{t} \le \text{Qty}_{t-1}$ following a loss trade | Hard assertion. System forbids increasing size to recoup losses. |
| 5 | **Lot-Size Aware Sizing** | $\text{allowed\_lots} = \left\lfloor \frac{\text{Capital} \times \text{RiskPerTradePct}}{|\text{Entry} - \text{Stop}| \times \text{LotSize}} \right\rfloor$ | If $\text{allowed\_lots} < 1$, reject trade (`REJECT_RISK_BELOW_MINIMUM_LOT`). |
| 6 | **Margin Sufficiency** | $\text{RequiredMargin}(\text{allowed\_lots}) \le \text{FreeMargin} \times 0.80$ | Reject trade (`REJECT_INSUFFICIENT_MARGIN`). |
| 7 | **Manual Trade Inclusion** | $\text{TotalExposure} = \text{AlphaForgeExposure} + \text{ManualBrokerExposure}$ | Total broker account exposure is factored in; manual trades reduce bot capacity. |
| 8 | **Exchange Session Boundary** | Session resets strictly at exchange opening boundary (e.g. 09:15 IST), NEVER local system midnight. | Prevents mid-overnight or desynchronized risk state resets. |

---

## 3. Lot-Size Calculation Math & Proof

Let:
- $C = \text{Account Capital}$
- $r = \text{Risk Fraction per Trade}$ (e.g., $0.01$ for $1\%$)
- $P_{entry} = \text{Calculated Entry Price}$
- $P_{stop} = \text{Calculated Stop-Loss Price}$
- $\Delta P_{stop} = |P_{entry} - P_{stop}|$
- $L = \text{Contract Lot Size}$ (e.g., $25$ or $50$ shares/contract for NIFTY futures)

The raw permissible quantity in contracts:
$$Q_{raw} = \frac{C \times r}{\Delta P_{stop} \times L}$$

The allocated lot count must be the discrete integer floor:
$$N_{lots} = \lfloor Q_{raw} \rfloor$$

If $N_{lots} < 1$:
The system **CANNOT** round up to 1 lot because doing so would violate the maximum risk limit $C \times r$. Therefore, the trade is rejected:
$$\text{Action} \longrightarrow \text{Emit } \mathbf{REJECT\_RISK\_BELOW\_MINIMUM\_LOT}$$

---

## 4. Emergency Kill Switch Architecture

The Risk Engine includes a multi-channel Emergency Kill Switch:
1. **Automated Trigger Conditions:**
   - Breach of Daily Loss Limit.
   - Unprotected position detected for $> 10$ seconds.
   - Broker reconciliation mismatch unresolvable after 3 attempts.
   - Repeated order rejections ($> 3$ consecutive rejected orders).
2. **Action upon Trigger:**
   - Instantly revoke all trading authorization tokens.
   - Cancel all open limit/entry orders.
   - Market exit all open unprotected positions if configured for fail-safe exit.
   - Raise critical P0 alarms to Telegram / SMS / Pager.
   - Transition system state to `HALTED_MANUAL_INTERVENTION_REQUIRED`.
