# AlphaForge — Phase 8: Idempotency, Crash Recovery & Broker Reconciliation

---

## 1. Phase 8 Purpose

Phase 8 introduces **Idempotency, Crash Recovery, and Broker Reconciliation** (`alphaforge.execution.idempotency`, `alphaforge.broker`, `alphaforge.reconciliation`), answering the core operational questions:
> **"How does the system guarantee that repeated calls, network timeouts, or process crashes never result in duplicate orders, and how does cold-boot startup verify local state against exchange facts before permitting trading?"**

---

## 2. Logical Order Identity vs. Attempt Identity

AlphaForge strictly separates:
1. **Logical Order Identity (`client_order_id`):**
   - Represents unique trade intent originating from an approved strategy signal and risk budget.
   - Remains immutable across network retries, timeouts, and process restarts.
2. **Execution Attempt Identity (`execution_attempt_id`):**
   - Represents an individual physical submission attempt (e.g. `AF-E-9A5316E53820837B7E14DAEF-ATTEMPT-1`).
   - Increments monotonically on subsequent retry dispatches without altering the underlying logical order ID.

---

## 3. Client Order ID Algorithm

The authoritative generator `generate_client_order_id()` produces deterministic IDs from canonical inputs:

```text
canonical_raw = f"{strategy_id}|{strategy_version}|{symbol}|{order_role}|{signal_id}"
digest = SHA-256(canonical_raw)
client_order_id = f"AF-{order_role[0]}-{digest[:24]}"
```

- **Format:** `AF-{E|S|X}-{24_HEX_CHARS}` (Total length = 29 characters $\le 32$).
- **Entropy:** 24 hexadecimal characters provide $16^{24} = 2^{96}$ collision resistance, rendering collision probability astronomically negligible.
- **Determinism:** Independent of wall clocks, process IDs, thread IDs, and random UUIDs.

---

## 4. Collision Protection (`IdempotencyRegistry`)

The `IdempotencyRegistry` maps `client_order_id` to its canonical `OrderIntent`:
- **Matching Intent:** A repeated registration request with identical intent parameters is recognized as an idempotent duplicate and returns the existing intent (NO-OP).
- **Conflicting Intent:** If the same `client_order_id` is submitted with conflicting fields (`symbol`, `side`, `quantity`, `role`, `signal_id`), the system fails closed immediately with `IdempotencyCollisionError`.
- Conflicting requests are never merged, overwritten, or silently accepted.

---

## 5. Broker Abstraction (`AbstractBroker`)

The `AbstractBroker` protocol defines the standard interface for broker communication:
- `submit_order(request: BrokerOrderRequest) -> BrokerOrder`
- `get_order(client_order_id, broker_order_id) -> BrokerOrder | None`
- `get_open_orders() -> tuple[BrokerOrder, ...]`
- `get_positions() -> tuple[BrokerPosition, ...]`
- `cancel_order(client_order_id: str) -> BrokerOrder`
- `is_available() -> bool`

The interface contains zero vendor-specific API structures or proprietary network calls.

---

## 6. Simulated Paper Broker (`PaperBroker`)

The `PaperBroker` provides an in-memory, thread-safe execution venue for testing:
- Tracks orders by both `client_order_id` and `broker_order_id`.
- Rejects conflicting duplicates with `BrokerOrderCollisionError`.
- **Single-Entry Model & Scale-In Prevention (V1 Invariant):**
  - Strictly enforces at-most-one active entry order per instrument (`_position_opening_order`).
  - Rejects any subsequent `ENTRY` order while a position is active (whether same-side scale-in or opposite-side flip) with `BrokerPositionConflictError`.
  - Rejects `EXIT` or `STOP` orders when no position is open or if order quantity exceeds active position quantity.
  - Rejects execution fills attempting to increase position beyond the opening entry order quantity.
- Simulates acknowledgements, partial fills, full fills, rejections, and cancellations.
- Tracks positions accurately from execution fills.
- Supports deterministic simulation of broker unavailability and post-acceptance timeouts.

---

## 7. Timeout-After-Acceptance Semantics

When a network timeout occurs during order submission:
1. The broker may have already accepted, assigned an order ID, and placed the order on the book.
2. The caller receives a `TimeoutError` and enters `UNKNOWN` state locally.
3. The system **never** blindly generates a new order ID or resubmits.
4. Instead, the reconciler queries the broker using the original `client_order_id`.
5. Upon finding the order, local state is synchronized to `ACKNOWLEDGED` or `FILLED`.

---

## 8. Persistence Model (`RecoverySnapshot`)

Crash recovery relies on `AtomicStateStore`, persisting a structured `RecoverySnapshot`:
- `schema_version`: Revision number for forward/backward compatibility.
- `orders`: Map of active `LocalOrderRecord` snapshots indexed by `client_order_id`.
- `positions`: Map of active `LocalPositionRecord` snapshots indexed by symbol.
- `reconciliation_gate_open`: Persisted gate status.
- `created_at`: Timezone-aware UTC timestamp.

---

## 9. Atomic Write Protocol

To prevent state corruption from mid-write process crashes:
1. Acquire store re-entrant lock.
2. Serialize validated snapshot to JSON string.
3. Write payload to temporary sibling file (`.tmp`).
4. Explicitly flush and `os.fsync()` file descriptor to guarantee disk synchronization.
5. Atomically replace the destination file using `os.replace()`.
6. Corrupted or truncated files fail closed on reload with `CorruptedStateError`.

---

## 10. Crash Scenario Matrix

| Scenario | Pre-Crash State | Broker State | Recovery Action | Gate State |
|---|---|---|---|---|
| A. Crash before submit | `CREATED` | Absent | Discard or resubmit with same `client_order_id` | Closed |
| B. Crash in-flight | `SUBMITTED` | Absent | Cancel locally / mark terminal | Closed |
| C. Post-acceptance timeout | `UNKNOWN` | `ACKNOWLEDGED` | Reconcile Case A $\rightarrow$ `ACKNOWLEDGED` | Open on complete match |
| D. Ack crash before persist | `SUBMITTED` | `ACKNOWLEDGED` | Reconcile Case A $\rightarrow$ `ACKNOWLEDGED` | Open on complete match |
| E. Partial fill crash | `UNKNOWN` | `PARTIALLY_FILLED` | Reconcile Case A $\rightarrow$ `PARTIALLY_FILLED` | Closed until protected |
| F. Full fill crash | `UNKNOWN` | `FILLED` | Reconcile Case A $\rightarrow$ `FILLED` $\rightarrow$ `PROTECTION_PENDING` | Closed until protected |
| G. Protection pending crash | `PROTECTION_PENDING` | Resting stop present | Reconcile $\rightarrow$ `PROTECTED` | Open |
| H. Protection confirmed crash | `PROTECTED` | Resting stop present | Reconcile $\rightarrow$ `PROTECTED` | Open |
| I. Exit pending crash | `EXIT_PENDING` | Exit open | Reconcile Case A $\rightarrow$ `EXIT_PENDING` | Closed |
| J. Full close crash | `CLOSED` | Flat | Reconcile Case D $\rightarrow$ `CLOSED` | Open |

---

## 11. Authoritative 9-Step Cold-Boot Reconciliation

The `ColdBootReconciler` executes the strict 9-step alignment sequence:
1. **Load local open orders** from atomic persistence store.
2. **Load local open positions** from atomic persistence store.
3. **Query broker open orders** via `broker.get_open_orders()`.
4. **Query broker positions** via `broker.get_positions()`.
5. **Compare local vs broker state** across all matching cases.
6. **Resolve deterministic matches** and align local Phase 7 FSM instances.
7. **Keep new entries BLOCKED** throughout execution.
8. **Verify protection for active positions** via `ProtectionWatchdog`.
9. **Resume only after reconciliation success** (`gate.open(result)`).

---

## 12. Local/Broker Matching Cases

- **Case A (Local exists + Broker exists):** Reconciles status and fill quantities.
- **Case B (Local exists + Broker missing):** Queries broker order history; confirms terminal or escalates.
- **Case C (Broker exists + Local missing):** Flags `UNKNOWN_EXTERNAL_ORDER`; fails closed to `MANUAL_ESCALATION`.
- **Case D (Matching position):** Quantities and directions match; verifies resting stop protection.
- **Case E (Mismatched position):** Local and broker quantities disagree; blocks entries, escalates.
- **Case F (Broker position exists + Local missing):** Flags `UNKNOWN_EXTERNAL_POSITION`; blocks entries, escalates.

---

## 13. Protection Re-Verification & Authoritative Broker Override

Active positions are never assumed protected based on stale memory, cached state, or local boolean flags:
1. **Local Flag Non-Authoritative:** Stale `local_pos.is_protected = True` is purely descriptive and has zero authority. Authoritative protection status is determined solely by validating resting stop orders on the broker's live order book.
2. **Authoritative Matching Hierarchy:**
   - **Symbol:** Order symbol must strictly match position symbol.
   - **Direction:** Stop direction must oppose the position (`LONG` position requires `SELL` stop; `SHORT` position requires `BUY` stop).
   - **Quantity:** Stop quantity must match the active position quantity exactly (partial protection is rejected).
   - **Role & Type:** Order role must be `OrderRole.STOP` and order type must be `SL` or `SL-M`.
   - **Active Status:** Order status must be active on the broker book (`ACKNOWLEDGED` or `PARTIALLY_FILLED`).
   - **Protection ID Matching:** First attempts match by explicit `protection_order_id` if known locally; falls back to matching resting stop meeting all physical criteria.
3. **Fail-Closed Gate Enforcement:** If an authoritative resting stop cannot be verified on the broker for an active position:
   - `is_protection_confirmed` is set to `False`.
   - Reconciler flags `ReconciliationAction.TRIGGER_EMERGENCY_PROTECTION` with `ReconciliationReasonCode.PROTECTION_UNCONFIRMED`.
   - `ReconciliationGate` remains strictly `CLOSED` (`new_entries_allowed = False`), blocking all new order submissions until protection is established.

---

## 14. Reconciliation Gate (`ReconciliationGate`)

- Starts in `CLOSED` state (`can_accept_new_entries() == False`).
- Opens **only** when `ReconciliationStatus == MATCHED` with zero mismatches, zero unknown entities, and verified protection.
- Any subsequent failure, timeout, or position mismatch locks the gate immediately.

---

## 15. Retry Policy

- `KNOWN_SUCCESS` / `KNOWN_FILL`: No retry needed.
- `KNOWN_REJECTION`: Strategy/risk notified; no blind retry.
- `UNKNOWN_OUTCOME`: Blind retry strictly prohibited; reconciler inspects broker state by `client_order_id` before any action.

---

## 16. Manual Escalation

When state cannot be unambiguously resolved:
- Transition order to `OrderState.MANUAL_ESCALATION`.
- Set `ReconciliationStatus.ESCALATED`.
- Keep `ReconciliationGate` locked.

---

## 17. Guarantees & Non-Claims

> [!IMPORTANT]
> **Authoritative Semantic Definition:**
> - AlphaForge enforces **at-most-one logical order identity** within the trading system.
> - Broker-side duplicate prevention is guaranteed by deterministic client identity and broker order book reconciliation.
> - When broker state is unavailable or contradictory, the system **fails closed**, keeps entries blocked, and mandates manual operator escalation.
> - Phase 8 does **NOT** claim guaranteed recovery when broker state is unavailable, nor does it claim real-world exactly-once delivery across unreliable non-idempotent brokers.

---

## 18. Phase 9 Boundary & Security Lock

- **No Audit Ledger / Hash Chain:** Deferred to Phase 9.
- **`LIVE_TRADING = FALSE`:** Strictly enforced across all modules.
- **Zero Real Broker Calls:** Zero Kite Connect, Zerodha, or network sockets.
