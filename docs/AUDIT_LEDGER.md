# AlphaForge — Phase 9: Immutable Audit Ledger & Execution Record

---

## 1. Phase 9 Purpose

Phase 9 introduces the **Deterministic, Append-Only, Tamper-Evident Audit Ledger** (`alphaforge.ledger`), answering the fundamental forensic questions:
> **"How does AlphaForge maintain an indisputable, mathematically verifiable historical record of every execution-critical decision, transition, and state change, and how can operators detect retroactive corruption or tampering down to the exact corrupted record?"**

The ledger provides an authoritative historical audit trail for:
- Forensic reconstruction and incident investigations.
- Post-crash post-mortem state analysis.
- Complete causal and correlation tracing across Strategy, Risk, Execution, Protection, and Reconciliation boundaries.
- Cryptographic verification of record immutability.
- Deterministic replay support in later testing phases.

### Non-Operational Authority Mandate
The ledger is an **authoritative historical record**, not an operational authority. The ledger **MUST NOT**:
- Generate trading signals or modify strategy rules.
- Evaluate risk limits or override risk circuit breakers.
- Alter cost or slippage calculations.
- Alter contract specification rules or validity.
- Dictate or change Order FSM transition legality.
- Open or close the Phase 8 reconciliation gate.
- Submit broker orders or execute live trades.

---

## 2. Architecture Overview

```text
+-------------------------------------------------------------------------------+
|                           Operational System Phases                           |
|  Phase 1: Strategy  -->  Phase 5: Risk  -->  Phase 7: FSM  -->  Phase 8: Recon  |
+-------------------------------------------------------------------------------+
           |                     |                  |                  |
           v                     v                  v                  v
     (Direct Emit)         (Direct Emit)     (FSM Auditor)     (Recon Auditor)
+-------------------------------------------------------------------------------+
|                      alphaforge.ledger.AuditLedger                            |
|  - Deterministic Logical Event Identity: compute_logical_event_id(...)         |
|  - Cryptographic Event Hash Chaining: compute_event_hash(...)                 |
|  - Strict 1-based Monotonic Sequence Allocation: 1, 2, 3... N                  |
|  - Event-Level Idempotency & Deduplication Engine                             |
|  - Concurrency Safety via threading.RLock                                     |
+-------------------------------------------------------------------------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v                                       v
    InMemoryLedgerStorage                        FileLedgerStorage
    (Testing / Transient)                       (Atomic Append JSONL + fsync)
```

---

## 3. Data Model & Event Schema

Every historical event is stored as an immutable, strictly validated Pydantic model (`AuditEvent`):

```python
class AuditEvent(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    event_id: str = Field(description="Deterministic logical event ID (EVT-...)")
    sequence_number: int = Field(ge=1, description="Strictly monotonic 1-based sequence")
    event_timestamp: datetime = Field(description="Timezone-aware UTC timestamp")
    event_type: AuditEventType = Field(description="Controlled taxonomy event type")
    entity_type: str = Field(description="Target entity type (e.g. ORDER, SIGNAL)")
    entity_id: str = Field(description="Target entity identifier")
    correlation_id: str = Field(description="Trace ID spanning full lifecycle")
    causation_id: str = Field(description="Direct causal trigger of this event")
    payload: dict[str, Any] = Field(description="Event data payload")
    previous_event_hash: str = Field(description="'GENESIS' or 64-hex parent hash")
    event_hash: str = Field(description="SHA-256 digest of entire canonical record")
```

### Model Invariants:
- `frozen=True` and `extra="forbid"`: Committed records cannot be mutated.
- All timestamps enforce timezone-aware UTC (`datetime.now(UTC)`).
- `event_id` must follow `EVT-[0-9A-F]{24}`.
- `previous_event_hash` must be `"GENESIS"` (for sequence 1) or a 64-character lowercase hexadecimal string.
- `event_hash` must be a 64-character lowercase hexadecimal string.

---

## 4. Controlled Event Taxonomy (`AuditEventType`)

AlphaForge enforces a strict 22-state closed event taxonomy spanning all execution-critical phases:

| Domain | Event Type | Description |
| :--- | :--- | :--- |
| **Strategy** | `SIGNAL_GENERATED` | Strategy rule criteria met; signal dispatched to risk |
| | `SIGNAL_REJECTED` | Strategy rejected due to filters, warmup, or data quality |
| **Risk** | `RISK_CHECK` | Pre-trade risk check evaluated |
| | `RISK_REJECTED` | Trade blocked by risk limits, drawdown, or fat-finger check |
| | `RISK_RESERVED` | Capital/margin successfully reserved |
| | `RISK_RELEASED` | Margin released upon fill, cancellation, or exit |
| | `CIRCUIT_BREAKER_TRIGGERED` | Global/symbol loss threshold breached; circuit breaker active |
| **Order FSM** | `ORDER_CREATED` | Order initialized in local memory |
| | `ORDER_VALIDATED` | Order attributes validated against contracts and tick sizes |
| | `ORDER_SUBMITTED` | Order dispatched to broker gateway |
| | `ORDER_ACKNOWLEDGED` | Broker ACK received; order resting on exchange |
| | `ORDER_PARTIAL_FILL` | Execution of partial order quantity |
| | `ORDER_FILLED` | Execution of full order quantity |
| | `ORDER_CANCELLED` | Order cancellation confirmed |
| | `ORDER_REJECTED` | Order rejected by broker or exchange |
| | `ORDER_UNKNOWN` | Network timeout or communication loss during order dispatch |
| **Protection** | `PROTECTION_PENDING` | Stop-loss order creation dispatched |
| | `PROTECTION_CONFIRMED` | Resting stop-loss confirmed active on broker |
| | `PROTECTION_UNCONFIRMED` | Resting stop-loss missing after fill grace period |
| | `PROTECTION_HAZARD` | Active position detected without confirmed protection |
| | `EMERGENCY_PROTECTION_TRIGGERED` | Watchdog triggered immediate emergency market liquidation |
| **Reconciliation**| `RECONCILIATION_STARTED` | Cold-boot or runtime state reconciliation initiated |
| | `RECONCILIATION_MATCHED` | All local and broker state matched; trading gate opened |
| | `RECONCILIATION_MISMATCH` | Discrepancy detected; trading gate locked |
| | `RECONCILIATION_FAILED` | Broker unavailable or internal recovery failed |
| | `MANUAL_ESCALATION` | Unresolvable conflict requiring operator intervention |

---

## 5. Logical Event Identity vs. Monotonic Sequence Number

AlphaForge strictly separates **Logical Event Identity** from **Storage Sequence Ordering**:

### 1. Logical Event Identity (`event_id`):
- Represents the unique logical event intent.
- **Algorithm:**
  $$\text{digest} = \text{SHA-256}\Big(\text{schema\_version} \parallel \text{event\_type} \parallel \text{entity\_type} \parallel \text{entity\_id} \parallel \text{correlation\_id} \parallel \text{causation\_id} \parallel \text{canonical\_payload}\Big)$$
  $$\text{event\_id} = \text{"EVT-" } \parallel \text{digest}[:24].\text{upper}()$$
- **Sequence Independence:** The `event_id` does **NOT** incorporate `sequence_number` or `event_timestamp`. If a process restarts or retries appending the same logical event, it generates the exact same `event_id`, enabling robust deduplication without sequence corruption.

### 2. Sequence Number (`sequence_number`):
- Represents physical append order within the ledger.
- Strictly 1-based, gapless, and monotonic ($1, 2, 3, \dots, N$).

---

## 6. Canonical JSON Serialization & Cryptographic Hash Chaining

To guarantee reproducible cryptographic hashing across platforms and runtimes, AlphaForge implements `canonical_json()`:
1. **Key Sorting:** Dictionary keys are recursively sorted lexicographically (`sort_keys=True`).
2. **Decimal Exactness:** Decimals are formatted using `f"{val:f}"` (without conversion to floating point, eliminating floating-point precision loss).
3. **Datetime Formatting:** Datetimes are converted to UTC and formatted as ISO-8601 strings (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).
4. **Compact Whitespace:** Zero extraneous spaces between delimiters (`separators=(",", ":")`).

### SHA-256 Event Hash:
The authoritative block digest is computed across all fields, including the previous event hash:
$$\text{event\_hash} = \text{SHA-256}\Big(\text{canonical\_json}(\text{schema\_version}, \text{sequence\_number}, \text{event\_timestamp}, \dots, \text{previous\_event\_hash})\Big)$$

### Genesis Invariant:
- For sequence 1: $\text{previous\_event\_hash} = \text{"GENESIS"}$.
- For sequence $k > 1$: $\text{previous\_event\_hash}_k = \text{event\_hash}_{k-1}$.

---

## 7. Event-Level Idempotency & Conflict Rejection

When appending to the `AuditLedger`:
1. **Exact Duplicate Append:**
   If an incoming event produces an `event_id` already present in the ledger, the ledger compares their canonical payloads. If identical, the append is recognized as an idempotent duplicate and returns the existing `AuditEvent` without creating a new sequence or modifying the chain.
2. **Conflicting Identity Collision:**
   If an incoming event matches an existing `event_id` but contains differing payload content, the ledger fails closed immediately and raises `LedgerIntegrityError`. Conflicting records are never merged, overwritten, or accepted.

---

## 8. Persistence & Storage Engines

The ledger provides two storage implementations adhering to `AbstractLedgerStorage`:

### 1. `InMemoryLedgerStorage`:
- Thread-safe volatile memory store for tests, simulations, and unit isolation.

### 2. `FileLedgerStorage`:
- Append-only JSON Lines (`ledger.jsonl`) store.
- **Durability Invariant:** Every append flushes memory buffers and forces an immediate OS-level fsync (`os.fsync(f.fileno())`) to guarantee disk durability before returning control.
- **Fail-Closed Loading:** On initialization, the storage scans all lines. If any line contains invalid JSON, incomplete records, or trailing empty lines, it raises `LedgerCorruptionError` immediately.

---

## 9. Cold-Boot Startup Verification & Tamper Detection

On startup, `AuditLedger` can automatically run `verify_chain()`:
- Verifies sequence continuity ($1 \dots N$ with zero gaps and zero duplicates).
- Verifies hash chaining ($\text{previous\_event\_hash}_i == \text{event\_hash}_{i-1}$).
- Verifies deterministic `event_id` derivation.
- Verifies `event_hash` calculation for every event.
- Verifies absence of conflicting duplicate event IDs.

If verification fails, `LedgerVerificationResult` provides rich diagnostic details:
- `valid: bool = False`
- `corruption_detected: bool = True`
- `corruption_sequence: int` (the exact sequence index of the corrupted record)
- `error_code: str` (`SEQUENCE_DISCONTINUITY`, `HASH_LINK_CORRUPTED`, `EVENT_HASH_CORRUPTED`, etc.)

---

## 10. Non-Invasive Observer Adapters

AlphaForge provides observer adapters to integrate existing Phase 7 and Phase 8 components without modifying their operational semantics:

### 1. `OrderFSMTransitionAuditor`:
- Attaches to `OrderStateMachine(..., transition_listener=auditor.on_transition)`.
- Translates `TransitionEvent` into `AuditEventType` without modifying FSM transition legality.
- **Audit Failure Isolation:** If audit recording raises an exception, the FSM state is already committed. The failure is recorded in `auditor.last_audit_error` and re-raised for application logging without corrupting or rolling back legal execution states.

### 2. `ReconciliationAuditor`:
- Translates `ReconciliationResult` from Phase 8 cold-boot checks into `RECONCILIATION_STARTED`, `RECONCILIATION_MATCHED`, or `RECONCILIATION_MISMATCH`.
- If an unconfirmed protection hazard is present, automatically records `PROTECTION_UNCONFIRMED` and `EMERGENCY_PROTECTION_TRIGGERED` audit events.

---

## 11. Concurrency Model

- `AuditLedger` uses a re-entrant lock (`threading.RLock`) guarding all append and query operations.
- Sequence allocation and hash computation are executed atomically under lock.
- Concurrency stress tests verify 100 concurrent appends across 10 threads generate strictly contiguous sequence numbers $1 \dots 100$ and a cryptographically valid hash chain.

---

## 12. Scope Boundaries & Future Roadmap

### Phase 9 Scope (Implemented):
- Canonical event models (`AuditEvent`, `AuditEventType`, `LedgerVerificationResult`).
- Authoritative exception hierarchy in `alphaforge.core.exceptions`.
- Canonical JSON serialization and deterministic hashing.
- Cryptographic hash chaining with Genesis convention.
- Event-level idempotency and conflict rejection.
- Durability engine (`InMemoryLedgerStorage`, `FileLedgerStorage` with `fsync`).
- Cold-boot startup verification and 15 tamper vector test suites.
- Non-invasive FSM and Reconciliation adapters.

### NOT Implemented in Phase 9 (Deferred to Phase 10+):
- **Phase 10:** Backtesting engine, portfolio-level simulations, quant analytics.
- **Phase 11:** Deterministic historical replay engine.
- **Phase 12:** Chaos testing and automated failure injection.
- **Phase 13:** Key management services (KMS), hardware security modules (HSM), regulatory reporting formats.
- **Phase 14:** OpenTelemetry, Prometheus metrics, distributed tracing.
- **Phase 15–17:** Multi-environment deployment, paper/shadow execution, live trading brokers (Zerodha Kite Connect, interactive brokers, live WebSockets).
- `LIVE_TRADING = FALSE` is strictly enforced across the entire repository.
