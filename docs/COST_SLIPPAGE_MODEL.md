# AlphaForge — Phase 6 Cost & Slippage Model V1

## Purpose
Phase 6 is a deterministic modeling layer that answers: for a proposed futures trade, what are the expected slippage, transaction costs, gross P&L, net P&L, and R-multiples?

This module does not place, modify, cancel, route, or reconcile orders.

## Scope
Supported components are entry slippage, exit slippage, entry transaction fee, exit transaction fee, fixed round-trip cost, total slippage cost, total round-trip cost, gross P&L, net P&L, gross R, and net R.

The model is broker-independent and accepts explicit reference prices. It has no market-data, broker, WebSocket, execution, replay, failure-injection, or live-trading dependency.

## Model assumptions
When no frozen market-specific cost schedule exists, Phase 6 V1 uses explicit zero defaults:

- `entry_fee_rate = Decimal("0")`
- `exit_fee_rate = Decimal("0")`
- `entry_slippage_rate = Decimal("0")`
- `exit_slippage_rate = Decimal("0")`
- `fixed_cost_per_trade = Decimal("0")`

These are **MODEL ASSUMPTIONS / NOT BROKER-VERIFIED**. They are not claims about Zerodha, NSE, statutory taxes, exchange charges, or any production tariff.

Rates are decimal fractions. For example, `Decimal("0.0005")` means 0.05%.

## Inputs and result
`CostInput` requires explicit side, entry/exit reference prices, quantity, contract multiplier, supplied monetary `risk_amount`, immutable `CostConfig`, and an explicit UTC timestamp. The timestamp is deliberately explicit so repeated identical calculations remain deterministic; no hidden wall-clock default is used.

`CostResult` records reference/effective prices, notionals, slippage costs, fees, fixed cost, transaction cost, total round-trip cost, gross/net P&L, supplied risk, R-multiples, calculation version, timestamp, and optional symbol/signal metadata.

## Formulas
Notional follows the Phase 5 authoritative convention:

`notional = execution_price * quantity * contract_multiplier`

### Side-aware slippage
For slippage rates `s_entry` and `s_exit`:

**LONG**
- `effective_entry = reference_entry * (1 + s_entry)`
- `effective_exit = reference_exit * (1 - s_exit)`

**SHORT**
- `effective_entry = reference_entry * (1 - s_entry)`
- `effective_exit = reference_exit * (1 + s_exit)`

Effective prices must remain finite and strictly positive. Therefore a LONG exit slippage rate of 1 or more, or a SHORT entry slippage rate of 1 or more, fails closed when it makes the effective price non-positive.

### Transaction cost
`entry_fee = entry_notional * entry_fee_rate`

`exit_fee = exit_notional * exit_fee_rate`

`transaction_cost = entry_fee + exit_fee + fixed_cost_per_trade`

`fixed_cost_per_trade` is **ROUND-TRIP** and charged exactly once.

### Explicit slippage cost
`entry_slippage_cost = abs(effective_entry - reference_entry) * quantity * contract_multiplier`

`exit_slippage_cost = abs(effective_exit - reference_exit) * quantity * contract_multiplier`

`total_slippage_cost = entry_slippage_cost + exit_slippage_cost`

### Gross P&L
**LONG**

`gross_pnl = (effective_exit - effective_entry) * quantity * contract_multiplier`

**SHORT**

`gross_pnl = (effective_entry - effective_exit) * quantity * contract_multiplier`

### Net P&L
`net_pnl = gross_pnl - transaction_cost`

Slippage is already reflected in the effective execution prices used for gross P&L. Consequently transaction cost contains fees plus the fixed round-trip charge, while the all-in modeled friction is:

`total_round_trip_cost = total_slippage_cost + transaction_cost`

### R-multiples
`gross_R = gross_pnl / risk_amount`

`net_R = net_pnl / risk_amount`

`risk_amount` is supplied by the caller and is never recomputed by Phase 6. It must be positive and finite.

## Validation / fail-closed rules
Reject missing or invalid inputs including non-finite Decimal values, zero/negative prices, zero/negative quantity, zero/negative contract multiplier, zero/negative risk amount, negative fee/slippage/fixed-cost configuration, and non-positive/non-finite effective prices.

Invalid model construction is rejected by strict immutable Pydantic models. Calculation-time failures use `CostValidationError` with a deterministic `CostReasonCode`.

## Decimal and rounding policy
All monetary and price arithmetic uses `Decimal`. No `float()` conversion is used and no binary floating-point arithmetic appears in the calculation path.

No intermediate monetary rounding is performed. Any presentation rounding must happen outside the model under an explicitly approved convention.

## Calculation version
Every result records `PHASE6_COST_V1`.

Any formula or semantic change requires a calculation-version change; the version must never silently remain unchanged after a formula change.

## Phase 5 boundary
Phase 6 does not change Phase 5 risk limits, stop-distance rules, sizing, reservation behavior, portfolio limits, or risk formulas. The Phase 5 monetary risk is used only as the caller-supplied denominator for `gross_R` and `net_R`.

## Phase 7 boundary
Phase 7 remains out of scope. Phase 6 introduces no order state machine, broker interface, idempotency layer, execution callback, or live-order behavior.

## Safety
Phase 6 is modeling-only and contains no broker connectivity or order submission capability. `LIVE_TRADING` remains false; this phase is paper/simulation analysis only.
