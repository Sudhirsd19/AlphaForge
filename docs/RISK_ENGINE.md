# AlphaForge — Deterministic Risk Engine V1 Architecture & Specification

---

## 1. Overview & Philosophy

The **AlphaForge Deterministic Risk Engine V1** (`alphaforge.risk`) provides absolute, mathematically rigorous pre-trade risk governance for index futures trading.

It answers exactly one question:
> **"Is this proposed trade mathematically and portfolio-risk-wise allowed?"**

### Absolute Phase Boundaries & Strict Non-Goals
The Risk Engine strictly governs risk and makes pre-trade clearance decisions. It:
- **MUST NOT** place, modify, cancel, route, or execute orders to brokers or external venues.
- **MUST NOT** integrate with external broker APIs (e.g. Zerodha Kite Connect, Interactive Brokers).
- **MUST NOT** connect to WebSockets, REST trading endpoints, or network execution services.
- **MUST NOT** maintain order state machines (`PENDING`, `SUBMITTED`, `PARTIALLY_FILLED`, etc.).
- **MUST NOT** simulate fills, slippage, market impact, or latency models.
- **MUST NOT** execute historical backtests or replay simulations.
- **MUST NOT** incorporate AI, machine learning, sentiment, or predictive pricing models.
- **MUST** strictly keep `LIVE_TRADING = False`.

---

## 2. Architecture & Design Principles

```
                 +-----------------------------------+
                 |           Strategy Signal         |
                 +-----------------+-----------------+
                                   |
                                   v
                 +-----------------------------------+
                 |      RiskInput (Immutable)        |
                 +-----------------+-----------------+
                                   |
                                   v
                 +-----------------------------------+
                 |      evaluate_trade_risk()        |  <--- Pure Deterministic Gate
                 |   (12 Sequential Fail-Closed      |  <--- Decimal Math Only
                 |               Gates)              |
                 +-----------------+-----------------+
                                   |
                    +--------------+--------------+
                    |                             |
                    v                             v
           [APPROVED / REJECTED]           [RiskEngine (Lock)]
                    |                             |
                    v                             v
             RiskDecision                  RiskReservation
              (Immutable)                    (In-Memory)
```

The Risk Engine architecture is decoupled into three clear layers:
1. **Pure Functional Math Calculator (`alphaforge.risk.calculator`):**
   Stateless functions executing fixed-point `Decimal` arithmetic for risk distance, stop percentage, risk budget, sizing, notional, daily loss, and capital buffers.
2. **Deterministic Pre-Trade Evaluation Gate (`evaluate_trade_risk`):**
   Pure, side-effect-free decision function executing 12 sequential fail-closed gates against `RiskInput`, `PortfolioRiskState`, and `RiskConfig`.
3. **Atomic Reservation Manager (`RiskEngine`):**
   Thread-safe in-memory coordinator using `threading.Lock()` providing atomic risk budget reservation, duplicate-signal idempotency, and reservation release.

---

## 3. Mathematical Specifications & Limits

### 3.1 Currency & Precision Standard
All prices, notionals, cash balances, and risk amounts operate strictly in Python `Decimal`. Binary floating-point arithmetic (`float`) is strictly prohibited in risk logic to prevent precision degradation and non-deterministic rounding artifacts.

### 3.2 Stop Distance & Direction Governance
For a proposed trade with entry price $P_{\text{entry}}$ and stop price $P_{\text{stop}}$:

$$\text{risk\_distance} = |P_{\text{entry}} - P_{\text{stop}}|$$

$$\text{stop\_distance\_pct} = \frac{\text{risk\_distance}}{P_{\text{entry}}}$$

- **Direction Consistency Gate:**
  - `LONG`: Requires $P_{\text{stop}} < P_{\text{entry}}$. If $P_{\text{stop}} \ge P_{\text{entry}}$, fail closed with `INVALID_STOP_DIRECTION`.
  - `SHORT`: Requires $P_{\text{stop}} > P_{\text{entry}}$. If $P_{\text{stop}} \le P_{\text{entry}}$, fail closed with `INVALID_STOP_DIRECTION`.
- **Stop Distance Bounds Gate:**
  - Minimum stop distance: $\text{stop\_distance\_pct} \ge 0.10\%$ (`minimum_stop_distance`). Below this fails with `STOP_DISTANCE_TOO_SMALL`.
  - Maximum stop distance: $\text{stop\_distance\_pct} \le 3.00\%$ (`maximum_stop_distance`). Above this fails with `STOP_DISTANCE_TOO_LARGE`.

### 3.3 Position Sizing, Lot-Sizing Policy & Contract Multiplier
- **Explicit Contract Multiplier (No Fallback):**
  `contract_multiplier` must be explicitly supplied for every trade. There is strictly no default (`Decimal("1")` fallback eliminated). Non-positive or non-finite multipliers strictly produce `INVALID` (`INVALID_CONTRACT`).
  $$r_{\text{unit}} = \text{risk\_distance} \times \text{contract\_multiplier}$$
- **Base Per-Trade Risk Budget:**
  $$R_{\text{max}} = \text{account\_equity} \times \text{max\_risk\_per\_trade} \quad (\text{default } 0.50\%)$$
- **Explicit Lot-Sizing Policy:**
  - **Policy 1 (Default: `allow_lot_flooring = False`):**
    Automated position sizing requires the raw affordable quantity $Q_{\text{raw}} = R_{\text{effective}} / r_{\text{unit}}$ to be an exact integer multiple of `lot_size`. If $Q_{\text{raw}} \pmod{\text{lot\_size}} \neq 0$, the trade is deterministically rejected with `INVALID_LOT_SIZE`.
  - **Policy 2 (`allow_lot_flooring = True`):**
    Automated sizing explicitly floors the raw quantity to whole lots:
    $$Q = \left\lfloor \frac{Q_{\text{raw}}}{\text{lot\_size}} \right\rfloor \times \text{lot\_size}$$
    The engine then recomputes actual trade risk $R_{\text{trade}} = Q \times r_{\text{unit}}$ and strictly verifies $R_{\text{trade}} \le R_{\text{effective}}$. Upward rounding is strictly impossible.
  - If $Q < \text{lot\_size}$, the trade is rejected with `POSITION_SIZE_TOO_SMALL`.
- **Explicit Sizing Validation:**
  When `proposed_quantity` is specified, it must satisfy:
  1. $Q > 0$ and $Q \pmod{\text{lot\_size}} == 0$. Otherwise rejected with `INVALID_LOT_SIZE`.
  2. Total monetary risk $R_{\text{trade}} = Q \times r_{\text{unit}} \le R_{\text{effective}}$. Otherwise rejected with `RISK_LIMIT_EXCEEDED`.

### 3.4 Notional Exposure Caps
- **Position Notional:**
  $$N_{\text{trade}} = P_{\text{entry}} \times Q \times \text{contract\_multiplier}$$
- **Single-Position Notional Limit:**
  $$N_{\text{trade}} \le \text{account\_equity} \times \text{max\_single\_position\_notional} \quad (\text{default } 20.0\%)$$
  Breach rejects with `POSITION_NOTIONAL_EXCEEDED`.
- **Portfolio Notional Limit:**
  $$N_{\text{reserved}} + N_{\text{trade}} \le \text{account\_equity} \times \text{max\_portfolio\_notional} \quad (\text{default } 100.0\%)$$
  Breach rejects with `PORTFOLIO_NOTIONAL_EXCEEDED`.

### 3.5 Authoritative Account & Capital State Consistency
`PortfolioRiskState` is the authoritative source of truth for account state.
Before evaluating trade risk, the engine performs explicit strict equality checks:
$$\text{trade\_input.account\_equity} == \text{portfolio\_state.account\_equity}$$
$$\text{trade\_input.available\_capital} == \text{portfolio\_state.available\_capital}$$
Any mismatch (inflated or reduced equity/capital) fails closed immediately with `INVALID` (`INVALID_EQUITY` or `INSUFFICIENT_CAPITAL`). The engine never chooses the larger value, never averages, and never falls back.

### 3.6 Portfolio Risk & Concurrency Limits
- **Portfolio Aggregate Risk:**
  $$R_{\text{total\_after}} = R_{\text{reserved}} + R_{\text{trade}} \le \text{account\_equity} \times \text{max\_portfolio\_risk} \quad (\text{default } 2.00\%)$$
  Breach rejects with `PORTFOLIO_RISK_EXCEEDED`.
- **Max Open Trades Barrier:**
  $$\text{open\_trades} + \text{active\_reservations} + 1 \le \text{max\_open\_trades} \quad (\text{default } 5)$$
  Breach rejects with `MAX_OPEN_TRADES_EXCEEDED`.

### 3.7 Available Capital & Risk Reserve Buffer
- **Collateral / Margin Requirement:**
  If not explicitly provided by broker margin rules, baseline margin is calculated as:
  $$C_{\text{base}} = N_{\text{trade}} \times 0.15 \quad (\text{15\% initial margin requirement})$$
- **Risk Reserve Buffer (5%):**
  $$C_{\text{required}} = C_{\text{base}} \times (1 + \text{risk\_reserve\_buffer}) \quad (\text{default } 5\%)$$
  If $\text{available\_capital} < C_{\text{required}}$, the trade is rejected with `INSUFFICIENT_CAPITAL`.

### 3.8 Daily Loss Limits & Throttling
Tracking daily loss relative to trading session opening equity:
$$\text{daily\_loss} = \max(0, \text{daily\_starting\_equity} - \text{current\_equity})$$
$$\text{daily\_loss\_pct} = \frac{\text{daily\_loss}}{\text{daily\_starting\_equity}}$$

1. **Daily Loss Hard Halt (default 3.00%):**
   If $\text{daily\_loss\_pct} \ge \text{daily\_loss\_hard\_limit}$, all new trades are halted immediately and rejected with `DAILY_LOSS_LIMIT_REACHED`.
2. **Daily Loss Soft Throttling (default 2.00%):**
   If $\text{daily\_loss\_pct} \ge \text{daily\_loss\_soft\_limit}$, the per-trade risk budget is halved (50%) and bounded by remaining room before hard limit:
   $$R_{\text{remaining}} = \max(0, \text{daily\_starting\_equity} \times \text{daily\_loss\_hard\_limit} - \text{daily\_loss})$$
   $$R_{\text{effective\_max}} = \min(R_{\text{max}} \times 0.50, R_{\text{remaining}})$$

### 3.9 Correlated Exposure Groups & Authoritative Existing Risk
Instruments sharing underlying market risk (e.g. `NIFTY` and `BANKNIFTY`) are configured into deterministic correlation groups.
Existing exposure that belongs to the group is authoritatively computed from both active reservations and portfolio aggregate reserved risk:

$$R_{\text{group\_res}} = \sum_{r \in \text{active\_reservations}, \, r.\text{symbol} \in G} r.\text{risk}$$
$$R_{\text{non\_group\_res}} = \sum_{r \in \text{active\_reservations}, \, r.\text{symbol} \notin G} r.\text{risk}$$
$$R_{\text{unaccounted}} = \max\left(0, \, R_{\text{portfolio\_reserved}} - (R_{\text{group\_res}} + R_{\text{non\_group\_res}})\right)$$
$$R_{\text{group\_before}} = R_{\text{group\_res}} + R_{\text{unaccounted}}$$
$$R_{\text{group\_after}} = R_{\text{group\_before}} + R_{\text{new\_trade}}$$

$$\frac{R_{\text{group\_after}}}{\text{account\_equity}} \le \text{max\_group\_risk}$$

This formulation guarantees:
- **No undercounting:** Unitemized portfolio reserved risk is safely attributed to group exposure.
- **No double counting:** Active reservations fully itemizing reserved risk produce $R_{\text{unaccounted}} = 0$.
- Breach deterministically rejects with `CORRELATED_RISK_EXCEEDED`.

---

## 4. Deterministic Machine-Readable Reason Codes

The engine returns one of 23 explicit machine-readable `RiskReasonCode` enumerations with every decision:

| Reason Code | Decision State | Description |
| :--- | :--- | :--- |
| `APPROVED` | `APPROVED` | Trade passes all risk limits and is cleared for reservation |
| `INVALID_EQUITY` | `INVALID` | Account equity is non-positive or non-finite |
| `INVALID_ENTRY_PRICE` | `INVALID` | Entry price is non-positive or non-finite |
| `INVALID_STOP_PRICE` | `INVALID` | Stop price is non-positive or non-finite |
| `INVALID_STOP_DIRECTION` | `REJECTED` | Stop price is on invalid side of entry (Long stop >= entry, Short stop <= entry) |
| `INVALID_RISK_DISTANCE` | `INVALID` | Risk distance is zero or non-finite |
| `STOP_DISTANCE_TOO_SMALL` | `REJECTED` | Stop distance is below configured minimum threshold (0.10%) |
| `STOP_DISTANCE_TOO_LARGE` | `REJECTED` | Stop distance is above configured maximum threshold (3.00%) |
| `RISK_LIMIT_EXCEEDED` | `REJECTED` | Trade monetary risk exceeds per-trade limit or throttled budget |
| `PORTFOLIO_RISK_EXCEEDED` | `REJECTED` | Aggregate portfolio risk would exceed configured max portfolio risk (2.00%) |
| `MAX_OPEN_TRADES_EXCEEDED` | `REJECTED` | Total active positions + reservations would exceed max open trades (5) |
| `POSITION_NOTIONAL_EXCEEDED` | `REJECTED` | Single position notional exceeds configured fraction of equity (20%) |
| `PORTFOLIO_NOTIONAL_EXCEEDED` | `REJECTED` | Total portfolio notional exceeds configured fraction of equity (100%) |
| `INSUFFICIENT_CAPITAL` | `REJECTED` | Available capital is less than required capital + 5% risk reserve buffer |
| `DAILY_LOSS_LIMIT_REACHED` | `REJECTED` | Daily loss has breached hard limit (3.00%); all new trading is halted |
| `CORRELATED_RISK_EXCEEDED` | `REJECTED` | Combined risk in correlated group exceeds configured group limit |
| `INVALID_CONTRACT` | `INVALID` | Contract multiplier or instrument metadata is non-positive or invalid |
| `LOT_SIZE_UNAVAILABLE` | `INVALID` | Instrument lot size is non-positive or unavailable |
| `INVALID_LOT_SIZE` | `REJECTED` | Proposed quantity is not an exact positive integer multiple of lot size |
| `POSITION_SIZE_TOO_SMALL` | `REJECTED` | Risk budget cannot accommodate even a single minimum lot size |
| `DUPLICATE_RISK_RESERVATION` | `REJECTED` | Proposed signal_id already holds an active risk reservation |
| `MISSING_REQUIRED_INPUT` | `INVALID` | Mandatory input parameters are missing or null |
| `UNKNOWN_RISK_STATE` | `INVALID` | Unhandled fallback error state |

---

## 5. Reservation Model & Thread Safety

### 5.1 Single Source of Truth (SSOT) Authority Model
In Phase 5 V1, **`RiskEngine._reservations` is the authoritative Single Source of Truth (SSOT)** for active risk reservations.
- `PortfolioRiskState` is an immutable snapshot DTO representing account state and base market positions.
- When `portfolio_state.active_reservations` are passed into `request_risk_reservation()` or `evaluate_trade_risk()`, the engine performs deterministic reconciliation and deduplication.
- Two independent authoritative sources of reservation truth are strictly forbidden.

### 5.2 Deduplication by Stable Identity
Every reservation has a deterministic identity:
- Stable `reservation_id` (e.g. `RES-<signal_id>-<symbol>`)
- Stable `signal_id`

During evaluation:
1. All reservations from `RiskEngine._reservations` and `portfolio_state.active_reservations` are reconciled.
2. Identical duplicate instances are collapsed so that each logical reservation is represented **exactly once**.
3. Any conflicting attributes (e.g. mismatched prices, quantities, risk, notional, or symbols) for the same `reservation_id` or `signal_id` are treated as an **inconsistent state** and fail closed immediately (`RiskDecisionState.INVALID` with `RiskReasonCode.UNKNOWN_RISK_STATE`).

### 5.3 Mathematical Double-Count Prevention
To eliminate double counting when reservations exist in both `RiskEngine` and `portfolio_state`:
- Let $R_{\text{port\_res}} = \sum_{r \in \text{dedup}(\text{port.active})} r.\text{monetary\_risk}$
- Let $N_{\text{port\_res}} = \sum_{r \in \text{dedup}(\text{port.active})} r.\text{notional}$
- Base unitemized market position risk:
  $$R_{\text{base}} = \max(0, \text{portfolio\_state.reserved\_risk} - R_{\text{port\_res}})$$
- Base unitemized market position notional:
  $$N_{\text{base}} = \max(0, \text{portfolio\_state.reserved\_notional} - N_{\text{port\_res}})$$
- Effective reserved risk and notional:
  $$R_{\text{effective}} = R_{\text{base}} + \sum_{r \in \text{canonical}} r.\text{monetary\_risk}$$
  $$N_{\text{effective}} = N_{\text{base}} + \sum_{r \in \text{canonical}} r.\text{notional}$$
- Effective active trade count:
  $$\text{active\_count} = \text{portfolio\_state.open\_trade\_count} + |\text{canonical}| + 1$$
- Correlated exposure: Group risk sums each canonical reservation in the group exactly once.

### 5.4 Idempotency & Concurrency Guarantees
- **Atomic Operations:** All reservation evaluations, checks, acquisitions, queries, and releases are protected by `threading.Lock()`.
- **Deterministic Idempotency:** Submitting the same `signal_id` or a trade that maps to an existing `reservation_id` is rejected immediately with `DUPLICATE_RISK_RESERVATION`.
- **Query & Release Flexibility:** Reservations can be inspected via `get_reservation(identifier)` or atomically released by `release_reservation(identifier)`.
- **Race Condition Immunity:** Concurrent threads submitting identical signals yield exactly one reservation; concurrent threads submitting different signals are bounded strictly by portfolio risk, notional, and trade count limits.

### 5.5 Restart & Volatility Semantics
In Phase 5 V1:
- All reservations in `RiskEngine` are strictly volatile and held in-memory.
- Crash recovery, disk persistence, and database reconciliation are intentionally **deferred to Phase 8**.
- On process restart or crash, active in-memory reservations are lost unless re-established by an external authoritative recovery mechanism in Phase 8.
