# AlphaForge — Futures Contract Validation Rules

## Document Overview
This document specifies the validation engine rules for futures contracts, lot sizing, and tick increment alignment in AlphaForge, conforming to Master Specification Section 10 and Phase 3 requirements.

---

## 1. Contract Master Metadata Validation

The function `validate_contract_master(contract: ContractMaster) -> None` enforces strict physical boundaries:

1. **Instance Type:** Must be a valid `ContractMaster` instance.
2. **Identifier Non-Emptiness:** `underlying_symbol` and `contract_id` must be non-empty uppercase strings.
3. **Lot Size Positivity:** `lot_size > 0` (strictly positive integer).
4. **Tick Size Positivity:** `tick_size > 0` (strictly positive finite `Decimal`).
5. **Contract Multiplier Positivity:** `contract_multiplier > 0` (strictly positive finite `Decimal`).
6. **Temporal Order:** Listing $\le$ Trading Start $<$ Trading End $\le$ Expiry.

---

## 2. Lot Sizing & Quantity Rules

The function `validate_lot_quantity(quantity: int, lot_size: int) -> None` deterministically validates order sizing:

### Invariants:
1. **Strict Positivity:** Both `lot_size` and `quantity` must be strictly positive integers ($> 0$).
2. **Sub-Lot Rejection (Rule I):**
   - An order quantity smaller than one contract lot size ($q < \text{lot\_size}$) is strictly prohibited.
   - Fails immediately with `ContractValidationError`.
3. **Integer Multiple Enforcement (Rule J):**
   - An order quantity must be an exact non-zero integer multiple of the contract lot size:
     $$q \pmod{\text{lot\_size}} == 0$$
   - Fractional lots (e.g. quantity 30 with lot size 25) are rejected with `ContractValidationError`.

---

## 3. Tick Size Alignment & Fail-Closed Price Policy

The functions `is_tick_aligned(price: Decimal, tick_size: Decimal) -> bool` and `validate_price_tick(price: Decimal, tick_size: Decimal) -> None` govern price validity.

### Invariants:
1. **Exact Fixed-Point Decimal Arithmetic:**
   - Both `price` and `tick_size` are evaluated as `Decimal`.
   - Modulo alignment check:
     $$\text{remainder} = \text{price} \pmod{\text{tick\_size}} == \text{Decimal("0")}$$
2. **Strict Fail-Closed Invariant (No Silent Rounding):**
   - Under no circumstances will a price misaligned with the exchange tick size be silently rounded, truncated, or floored.
   - Any misalignment raises `ContractValidationError`:
     ```text
     ContractValidationError: Price 24500.03 violates tick increment alignment for tick_size 0.05
     ```
   - Rationale: Silent price adjustment creates unhedged execution drift and breaks deterministic trade simulation.
3. **Finite Positive Price Bounds:**
   - Non-finite (`NaN`, `Infinity`), negative, or zero prices are rejected immediately.
