# AlphaForge — Contract Master Specification

## Document Overview
This document specifies the canonical schema, numeric precision, and domain invariants for futures contracts in the AlphaForge trading system, conforming to Master Specification Section 10 and Phase 3 requirements.

---

## 1. Canonical Contract Master Schema

The contract master model (`alphaforge.contract.models.ContractMaster`) represents immutable metadata for a tradeable futures instrument. It is defined as a frozen Pydantic model with strict validation (`extra="forbid"`, `strict=True`).

| # | Field Name | Type | Precision / Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `exchange` | `str` | Uppercase, non-empty (e.g. `NSE`, `CME`) | Execution venue / exchange |
| 2 | `segment` | `str` | Uppercase, non-empty (e.g. `NFO`, `COM`) | Exchange market segment |
| 3 | `underlying_symbol` | `str` | Uppercase, non-empty (e.g. `NIFTY`) | Canonical underlying index or asset ticker |
| 4 | `contract_id` | `str` | Uppercase, unique identifier | Canonical contract ticker (e.g. `NIFTY26JUNFUT`) |
| 5 | `instrument_type` | `InstrumentType` | `INDEX \| FUTURES \| EQUITY` | Instrument classification (default `FUTURES`) |
| 6 | `listing_datetime` | `datetime` | Explicit UTC (`tzinfo=UTC`) | Timestamp contract was first registered / listed |
| 7 | `trading_start_datetime` | `datetime` | Explicit UTC (`tzinfo=UTC`) | Earliest timestamp trading execution is permitted |
| 8 | `trading_end_datetime` | `datetime` | Explicit UTC (`tzinfo=UTC`) | Latest timestamp trading execution is permitted |
| 9 | `expiry_datetime` | `datetime` | Explicit UTC (`tzinfo=UTC`) | Final contract expiry and cash settlement timestamp |
| 10 | `lot_size` | `int` | Strictly positive (`gt=0`) | Minimum tradeable lot size (e.g. 25) |
| 11 | `tick_size` | `Decimal` | Strictly positive finite Decimal | Minimum price increment (e.g. `Decimal("0.05")`) |
| 12 | `contract_multiplier` | `Decimal` | Strictly positive finite Decimal | Point value multiplier (e.g. `Decimal("1")`) |
| 13 | `price_decimal_places` | `int` | Non-negative integer (`ge=0`) | Price precision display places (default 2) |
| 14 | `quantity_decimal_places` | `int` | Non-negative integer (`ge=0`) | Quantity precision display places (default 0) |
| 15 | `currency` | `str` | Uppercase non-empty (e.g. `INR`, `USD`) | Base settlement currency |
| 16 | `settlement_type` | `SettlementType` | `CASH \| PHYSICAL` | Settlement mechanism (default `CASH`) |
| 17 | `status` | `ContractStatus` | Declared base status enum | Declared contract state (default `ACTIVE`) |
| 18 | `data_source` | `str` | Non-empty string | Provenance of contract reference metadata |
| 19 | `contract_version` | `int` | Non-negative integer | Schema/metadata revision number (default 1) |
| 20 | `is_suspended` | `bool` | Boolean flag | Venue trading suspension / halt status |

---

## 2. Invariants and Boundaries

### 2.1 Fixed-Point Decimal Arithmetic
- `tick_size` and `contract_multiplier` must be finite, non-zero, positive `Decimal` instances.
- Floating-point representations (`float`) are strictly forbidden to eliminate binary floating-point rounding errors in contract value calculations.

### 2.2 Explicit UTC Timezone Awareness
- All datetime fields (`listing_datetime`, `trading_start_datetime`, `trading_end_datetime`, `expiry_datetime`) must have explicit UTC timezone info (`tzinfo=UTC`).
- Naive datetimes and non-UTC timezones are rejected immediately with `ContractValidationError`.

### 2.3 Temporal Lifecycle Ordering
Every contract master record must satisfy the strict temporal lifecycle inequality:
$$\text{listing\_datetime} \le \text{trading\_start\_datetime} < \text{trading\_end\_datetime} \le \text{expiry\_datetime}$$

Any violation (such as trading start before listing, trading end before start, or trading end after expiry) raises `ContractValidationError`.

---

## 3. Metadata Provider & Repository Boundary

The contract metadata layer is decoupled from underlying databases or feeds via the `ContractMetadataProvider` protocol:

```python
class ContractMetadataProvider(Protocol):
    def get_contract(self, contract_id: str) -> ContractMaster | None: ...
    def get_contracts_for_underlying(self, underlying_symbol: str) -> list[ContractMaster]: ...
    def get_all_contracts(self) -> list[ContractMaster]: ...
```

The system provides `InMemoryContractMasterRepository` which guarantees:
1. **Uniqueness:** Contracts are indexed by unique `contract_id`.
2. **Idempotency:** Re-registering an identical record succeeds silently.
3. **Conflict Rejection:** Registering conflicting metadata for an existing `contract_id` raises `ContractValidationError`.
4. **Deterministic Ordering:** Underlying queries return contracts sorted ascending by `(expiry_datetime, contract_id)`.
