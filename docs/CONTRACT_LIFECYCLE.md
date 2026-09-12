# AlphaForge — Futures Contract Lifecycle Engine

## Document Overview
This document specifies the deterministic state machine, temporal boundary semantics, tradeability guards, and rollover eligibility rules for futures contracts within the AlphaForge system.

---

## 1. Lifecycle State Definitions

The lifecycle state is represented by `alphaforge.contract.enums.ContractStatus`:

```text
               ┌──────────────────────┐
               │    NOT_YET_LISTED    │  (t < trading_start)
               └──────────┬───────────┘
                          │ (t >= trading_start)
                          ▼
               ┌──────────────────────┐
    ┌─────────►│        ACTIVE        │◄────────┐
    │          └──────────┬───────────┘         │
    │                     │                     │
(unsuspend)               │ (t_to_expiry <= w)  │ (unsuspend)
    │                     ▼                     │
    │          ┌──────────────────────┐         │
    │          │       EXPIRING       │         │
    │          └──────────┬───────────┘         │
    │                     │                     │
    │  (is_suspended=True)│                     │
    │          ┌──────────▼───────────┐         │
    └──────────┤      SUSPENDED       ├─────────┘
               └──────────┬───────────┘
                          │ (t >= trading_end OR t >= expiry)
                          ▼
               ┌──────────────────────┐
               │       EXPIRED        │  (Terminal State)
               └──────────────────────┘
```

| State | Description | Tradeable by Default? |
| :--- | :--- | :---: |
| `NOT_YET_LISTED` | Evaluation timestamp is before listing or before trading start session | **No** |
| `ACTIVE` | Within open trading window; contract is normal tradeable state | **Yes** |
| `EXPIRING` | Within configured close-out window (e.g. 2 hours before expiry) | **No** (unless explicitly allowed) |
| `EXPIRED` | At or past trading session close or settlement timestamp | **No** |
| `SUSPENDED` | Venue or exchange has halted trading in this instrument | **No** |
| `INVALID` | Contract metadata breached schema or physical validation rules | **No** |
| `UNKNOWN` | Contract status cannot be determined | **No** |

---

## 2. Declared Status Precedence & Fail-Closed Evaluation

**Core Safety Invariant:**
> Declared contract status `INVALID`/`UNKNOWN`/`SUSPENDED` is fail-closed and takes precedence over timestamp-derived lifecycle evaluation.

### Interaction Between Declared Status and Timestamp Lifecycle:
1. **`INVALID`:** If declared `contract.status == ContractStatus.INVALID`, the contract evaluates strictly as `ContractStatus.INVALID`.
2. **`UNKNOWN`:** If declared `contract.status == ContractStatus.UNKNOWN`, the contract evaluates strictly as `ContractStatus.UNKNOWN`.
3. **`SUSPENDED`:** If declared `contract.status == ContractStatus.SUSPENDED` or `contract.is_suspended` is `True`, the contract evaluates strictly as `ContractStatus.SUSPENDED`.
4. **`EXPIRED`:** If declared `contract.status == ContractStatus.EXPIRED`, the contract evaluates strictly as `ContractStatus.EXPIRED`.
5. **Timestamp Lifecycle (Normal States):** If declared status is `NOT_YET_LISTED` or `ACTIVE`, timestamp rules deterministically dictate the current state (`NOT_YET_LISTED`, `ACTIVE`, `EXPIRING`, or `EXPIRED`).

Under no circumstances can a contract with declared `INVALID`, `UNKNOWN`, or `SUSPENDED` status evaluate as `ACTIVE` or `EXPIRING`, regardless of how valid or active its timestamps may appear.

---

## 3. Temporal Boundary Semantics

The lifecycle engine calculates state purely and deterministically from the contract metadata and an explicit evaluation timestamp $t$:

```python
def evaluate_contract_lifecycle(
    contract: ContractMaster,
    evaluation_timestamp: datetime,
    expiring_window: timedelta = timedelta(hours=2),
) -> ContractStatus
```

### Boundary Precision:
1. **`listing_datetime` (Inclusive):**
   - At $t = \text{listing\_datetime}$, `is_listed(contract, t)` returns `True`.
   - The contract remains `NOT_YET_LISTED` until trading start session.
2. **`trading_start_datetime` (Inclusive):**
   - At $t = \text{trading\_start\_datetime}$, the contract transitions from `NOT_YET_LISTED` to `ACTIVE`.
3. **`trading_end_datetime` (Exclusive):**
   - At $t = \text{trading\_end\_datetime}$, active trading immediately terminates. The contract transitions to `EXPIRED`.
4. **`expiry_datetime` (Exclusive):**
   - At $t = \text{expiry\_datetime}$, the contract is in terminal `EXPIRED` state.

---

## 3. Tradeability & Execution Safety Gate

The function `is_tradeable(contract, evaluation_timestamp, allow_expiring=False)` enforces a strict fail-closed policy:

- Returns `True` **only** if evaluated status is `ACTIVE` (or `EXPIRING` if and only if `allow_expiring=True`).
- Returns `False` for `NOT_YET_LISTED`, `EXPIRED`, `SUSPENDED`, `INVALID`, or `UNKNOWN`.
- Eliminates any risk of trading halted, unlisted, or expired contracts.

---

## 4. Multi-Contract & Rollover Resolution

When multiple contracts are listed for an underlying (e.g. Near-Month, Next-Month, Far-Month):

1. **`get_active_contracts(contracts, t)`:**
   - Filters contracts that are `ACTIVE` or `EXPIRING`.
   - Deterministically sorts them by `(expiry_datetime, contract_id)` ascending.
2. **`get_current_active_contract(contracts, t)`:**
   - Returns the front-month (nearest active expiry) contract.
3. **`get_next_contract(contracts, t)`:**
   - Returns the second nearest active expiry contract.
4. **`get_rollover_candidate(contracts, t)`:**
   - Identifies whether a rollover transition should be scheduled.
   - Evaluates to `(front_month, next_month)` when the front contract enters `EXPIRING` and the next contract is `ACTIVE`.
   - **Boundary Enforcement:** Informational only. Rollover candidate identification does not place orders or initiate execution.
