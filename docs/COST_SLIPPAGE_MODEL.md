# AlphaForge — Deterministic Cost & Slippage Model V1 Specification

---

## 1. Overview & Philosophy

The **AlphaForge Cost & Slippage Model V1** (`alphaforge.cost`) provides a deterministic, side-aware analytical model for transaction frictions, execution slippage, gross/net monetary P&L, and R-multiples for futures contracts.

It answers exactly one question:
> **"For a proposed futures trade, what is the expected transaction cost, expected execution slippage, and resulting net monetary P&L after costs?"**

### Absolute Phase Boundaries & Strict Non-Goals
The Cost & Slippage Model is a pure pre-trade and post-analysis financial modeling component. It:
- **MUST NOT** place, modify, cancel, route, or execute orders to brokers or external venues.
- **MUST NOT** integrate with broker APIs (e.g. Zerodha Kite Connect, Interactive Brokers).
- **MUST NOT** connect to WebSockets, market data streams, or network endpoints.
- **MUST NOT** maintain order state machines (`PENDING`, `SUBMITTED`, `FILLED`, etc.).
- **MUST NOT** simulate live fills, queue position, dynamic market impact, or latency.
- **MUST NOT** incorporate AI, machine learning, sentiment, or predictive pricing models.
- **MUST** strictly maintain `LIVE_TRADING = False`.

---

## 2. Architecture & Design Principles

```
                 +-----------------------------------+
                 |             CostInput             |
                 | (TradeSide, RefPrices, Qty, Mult) |
                 +-----------------+-----------------+
                                   |
                                   v
                 +-----------------------------------+
                 |       evaluate_trade_cost()       |
                 |   (Deterministic Gate Pipeline)   |
                 +-----------------+-----------------+
                                   |
                     +-------------+-------------+
                     |                           |
                     v                           v
          [Valid Inputs & Price]       [Invalid Input / Rate]
                     |                           |
                     v                           v
              CostResult (VALID)          CostResult (INVALID)
           - Effective Prices          - Null numeric fields
           - Notionals & Fees          - Deterministic reason code
           - Gross & Net P&L           - Fail-closed audit record
           - Gross & Net R
```

The Cost & Slippage module is organized into three decoupled layers:
1. **Pure Functional Math Calculator (`alphaforge.cost.calculator`):**
   Stateless functions executing fixed-point `Decimal` arithmetic for side-aware effective prices, fees, round-trip transaction costs, gross P&L, net P&L, and R-multiples.
2. **Deterministic Evaluation Engine (`alphaforge.cost.engine`):**
   Pure decision function `evaluate_trade_cost` and wrapper `CostEngine` verifying inputs, executing sequential fail-closed validations, and generating immutable `CostResult` audit records.
3. **Canonical Domain Models & Enumerations (`alphaforge.cost.models`, `alphaforge.cost.enums`):**
   Pydantic models strictly configured with `frozen=True`, `extra="forbid"`, `strict=True`, and deterministic enum reason codes.

---

## 3. Mathematical Specifications & Formulas

### 3.1 Currency & Precision Standard
All prices, notionals, fee rates, slippage rates, monetary costs, P&L, and risk amounts operate strictly in Python `Decimal`. Binary floating-point arithmetic (`float`) is strictly prohibited in cost calculation to prevent non-deterministic rounding and floating-point inaccuracy.

Exact `Decimal` arithmetic is preserved throughout the pipeline without premature rounding or artificial truncation.

### 3.2 Side-Aware Execution Slippage Model
Slippage models adverse price movement during execution. Because market friction acts against the trader:
- **Buying (LONG Entry or SHORT Exit):** The trader pays a higher price than the reference price.
- **Selling (LONG Exit or SHORT Entry):** The trader receives a lower price than the reference price.

Let:
- $P_{\text{ref, entry}}$ = Reference entry price
- $P_{\text{ref, exit}}$ = Reference exit price
- $s_{\text{entry}}$ = Entry slippage rate fraction ($\ge 0$)
- $s_{\text{exit}}$ = Exit slippage rate fraction ($\ge 0$)

#### For LONG Positions:
$$\text{effective\_entry\_price} = P_{\text{ref, entry}} \times (1 + s_{\text{entry}})$$
$$\text{effective\_exit\_price} = P_{\text{ref, exit}} \times (1 - s_{\text{exit}})$$

#### For SHORT Positions:
$$\text{effective\_entry\_price} = P_{\text{ref, entry}} \times (1 - s_{\text{entry}})$$
$$\text{effective\_exit\_price} = P_{\text{ref, exit}} \times (1 + s_{\text{exit}})$$

If any calculated effective execution price is non-positive ($P_{\text{eff}} \le 0$), the model fails closed with reason code `EFFECTIVE_PRICE_NON_POSITIVE`.

### 3.3 Notional Exposure
Notional value is calculated using the Phase 5 authoritative `calculate_notional`:
$$\text{entry\_notional} = \text{effective\_entry\_price} \times Q \times M$$
$$\text{exit\_notional} = \text{effective\_exit\_price} \times Q \times M$$
where $Q$ is trade quantity in units ($Q > 0$) and $M$ is the contract multiplier ($M > 0$).

### 3.4 Fee & Transaction Cost Model
Transaction costs include proportional exchange/clearing fees and fixed per-trade friction:
$$\text{entry\_fee} = \text{entry\_notional} \times f_{\text{entry}}$$
$$\text{exit\_fee} = \text{exit\_notional} \times f_{\text{exit}}$$
$$\text{fixed\_cost} = \text{fixed\_cost\_per\_trade}$$
$$\text{transaction\_cost} = \text{entry\_fee} + \text{exit\_fee} + \text{fixed\_cost}$$
where $f_{\text{entry}}$ and $f_{\text{exit}}$ are fee rates ($\ge 0$), and $\text{fixed\_cost\_per\_trade} \ge 0$.

### 3.5 Gross and Net P&L
Gross P&L measures trading gains/losses purely on effective execution prices:
- **LONG:**
  $$\text{gross\_pnl} = (\text{effective\_exit\_price} - \text{effective\_entry\_price}) \times Q \times M$$
- **SHORT:**
  $$\text{gross\_pnl} = (\text{effective\_entry\_price} - \text{effective\_exit\_price}) \times Q \times M$$

Net P&L deducts total transaction friction from Gross P&L:
$$\text{net\_pnl} = \text{gross\_pnl} - \text{transaction\_cost}$$

### 3.6 R-Multiples
R-multiples normalize monetary return relative to the initial monetary risk budget ($R_{\text{amount}} > 0$):
$$\text{gross\_R} = \frac{\text{gross\_pnl}}{\text{risk\_amount}}$$
$$\text{net\_R} = \frac{\text{net\_pnl}}{\text{risk\_amount}}$$

---

## 4. Fail-Closed Principles & Validation Invariants

1. **Explicit Multiplier Requirement:** Contract multiplier must be explicitly provided ($M > 0$). No default or fallback to 1 is allowed.
2. **Explicit Risk Amount:** Risk amount must be strictly positive ($R_{\text{amount}} > 0$).
3. **No Synthetic Null Values:** When an evaluation fails (`decision = INVALID`), all calculated monetary and ratio fields in `CostResult` are set to `None`. No synthetic numbers (e.g. 0, 1) are substituted.
4. **Model Assumptions vs. Broker Rates:**
   Default configuration values are initialized to zero:
   - `entry_fee_rate = 0.0`
   - `exit_fee_rate = 0.0`
   - `entry_slippage_rate = 0.0`
   - `exit_slippage_rate = 0.0`
   - `fixed_cost_per_trade = 0.0`
   These are explicitly documented as **MODEL ASSUMPTIONS / NOT BROKER-VERIFIED**. Callers must supply verified fee and slippage configurations for target execution venues.
5. **UTC Governance:** All timestamps must be timezone-aware UTC.
6. **Engine Versioning:** Calculation engine version is fixed at `PHASE6_COST_V1`.

---

## 5. Traceability & Acceptance

Every cost evaluation produces a frozen `CostResult` audit record containing:
- Decision outcome (`VALID` or `INVALID`)
- Machine-readable reason code (`CostReasonCode`)
- Complete price lineage (reference vs effective prices)
- Fee and friction breakdown (entry fee, exit fee, fixed cost, transaction cost)
- P&L and risk-adjusted metrics (gross/net P&L, gross/net R)
- Calculation version and UTC timestamp
