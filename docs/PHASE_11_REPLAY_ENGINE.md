# AlphaForge — Phase 11: Deterministic Replay Engine

---

## 1. Purpose & Scope Boundary

Phase 11 introduces the **Deterministic, Offline, Audit-Grade Replay Subsystem** (`alphaforge.replay`), fulfilling the core architectural requirement:
> **"Given any previously recorded, immutable audit ledger stream or backtest execution trace from AlphaForge, can the system reproduce the exact operational sequence, reconstruct the identical financial state, and verify end-to-end cryptographic and FSM integrity without modifying frozen trading/risk logic or accessing external services?"**

### The Core Forensic Principle
> **"Replay must RECONSTRUCT, not COPY."**

1. The source `AuditLedger` and its audit events are the **authoritative replay inputs**.
2. The source execution trace and source `BacktestResult` are reference artifacts for validation only. They are **never** used as hidden reconstruction shortcuts.
3. Replay engine initializes `ReplayState` with an empty trace (`trace=()`), zero trades (`trades=()`), and empty equity curve (`equity_curve=()`).
4. Replay trace is built event-by-event exclusively from authoritative audit events.
5. All `BacktestTrade` objects, performance metrics, and final `BacktestResult` are reconstructed independently and evaluated against the manifest's canonical hashes.

### Offline-Only Architectural Mandate
The Replay Engine is strictly an **offline, post-facto forensic verification engine**. Under no circumstances does it:
- Connect to live broker APIs, exchange gateways, WebSocket streams, or network endpoints.
- Require or accept live broker credentials, secret keys, or authentication tokens.
- Mutate, re-order, or append to source artifacts or audit ledgers.
- Modify frozen Phase 1–10 formulas, invariants, or domain models (Strategy, Risk, Contract, FSM, Cost, Backtest).
- Place live trades or execute live orders (`LIVE_TRADING = False` is strictly enforced).
- Implement Phase 12+ capabilities (Chaos / Failure Injection, Shadow Trading, Live Trading).

---

## 2. Architecture & Components

```text
+-------------------------------------------------------------------------------+
|                             Source Artifact Source                            |
|     (AuditLedger, Iterable[AuditEvent], BacktestResult, or .jsonl storage)    |
+-------------------------------------------------------------------------------+
                                        | (Read-Only Ingestion)
                                        v
+-------------------------------------------------------------------------------+
|                       ReplayArtifactSource & Manifest                         |
|  - Validates schema_version == "1.0.0"                                        |
|  - Computes deterministic replay_id: REPLAY-[24 HEX]                          |
|  - Canonical SHA-256 over source_run_id, event count, and boundary hashes     |
+-------------------------------------------------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                           ReplayEngine (Event Loop)                           |
|  1. Ingestion & Pre-Cutoff Filtering (Mode A: Full, Mode B: Validation, C: Prefix)
|  2. Cryptographic Hash Chain & Monotonicity Verification                     |
|  3. FSM Transition Legality & Terminal State Protection                       |
|  4. In-Memory Domain State Reconstruction (Orders, Position, Portfolio, Risk) |
|  5. Periodic & Trade-Boundary Checkpoint Capture (with deepcopy isolation)    |
+-------------------------------------------------------------------------------+
                                        |
         +------------------------------+------------------------------+
         |                                                             |
         v                                                             v
+----------------------------------+          +---------------------------------+
|      Post-Replay Validation      |          |     Diagnostic Forensics        |
|  - Execution Trace Hash Check    |          |  - First-Divergence Isolation   |
|  - Result Canonical Hash Check   |          |  - ReplayMismatch Reporting     |
|  - Gate E Contract Expiry Verify |          |  - state_before & state_after   |
+----------------------------------+          +---------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                     ReplayResult (Immutable Pydantic Model)                   |
|  - status: PASS | WARNING | FAIL                                              |
|  - final_state_fingerprint: SHA-256 canonical hash of reconstructed state     |
|  - checkpoints: Tuple[ReplayCheckpoint, ...]                                  |
|  - first_divergence: Optional[ReplayMismatch]                                 |
+-------------------------------------------------------------------------------+
```

---

## 3. Replay Lifecycle

The replay engine executes across six deterministic lifecycle stages:

1. **Ingest & Inspect Artifacts (`Load`)**:
   - `ReplayArtifactSource` extracts events, execution trace, and backtest results without mutating storage.
   - Computes `ReplayManifest` using canonical serialization and derives `replay_id` (`REPLAY-[A-F0-9]{24}`).
   - Verifies `schema_version` matches `1.0.0`.
2. **Verify Stream Lineage & Cryptography (`Verify`)**:
   - `AuditIntegrityValidator` enforces strictly sequential sequence numbers ($1, 2, \dots, N$).
   - Re-computes canonical logical `event_id` and SHA-256 `event_hash`.
   - Verifies previous event hash chaining (`event[i].previous_event_hash == event[i-1].event_hash`).
   - Checks strict timestamp monotonicity ($t_i \ge t_{i-1}$) and causal lineage existence.
3. **Reconstruct Domain State (`Reconstruct`)**:
   - If not in `VALIDATION_ONLY` mode, applies deterministic state mutations across:
     - Orders: lifecycle progression, prices, quantities, fills.
     - Position: direction, exposure, entry/exit prices, fees, slippage.
     - Portfolio: initial capital, cash, equity, cumulative fees, slippage, realized PnL.
     - Contract: identifier, multiplier, expiration timestamp, post-expiry fills, lifecycle violations.
     - Risk: evaluation counts, approvals, rejections, circuit breaker state.
4. **Capture Deterministic Checkpoints (`Checkpoint`)**:
   - At configured sequence intervals (`checkpoint_interval`) or event boundaries (`TRADE_CLOSED`, `BACKTEST_COMPLETED`), deep-copies the running state into `ReplayCheckpoint`.
   - Computes canonical SHA-256 fingerprint for each checkpoint.
5. **Cross-Validate Replayed Artifacts (`Compare`)**:
   - Compares reconstructed execution trace hash against source trace hash.
   - Compares source backtest result hash against replay result hash.
   - Verifies Gate E contract invariants (`post_expiry_fills == 0`).
6. **Report Forensic Summary (`Report`)**:
   - Returns immutable `ReplayResult` containing final status (`PASS`, `WARNING`, `FAIL`), checkpoints, warnings, errors, and first-divergence details (`ReplayMismatch`).

---

## 4. Source Artifact Contracts & Ingestion Schemas

### `ReplayArtifactSource`
Supports heterogeneous read-only sources:
- In-memory `AuditEvent` sequences or `InMemoryLedgerStorage`.
- File paths pointing to `.jsonl` audit dumps.
- Complete Phase 10 `BacktestResult` objects.

```python
source = ReplayArtifactSource(
    events=events,
    trace=execution_trace,
    backtest_result=backtest_result,
)
```

### `ReplayManifest`
```python
class ReplayManifest(BaseModel):
    source_run_id: str
    source_event_count: int
    source_start_time: datetime | None
    source_end_time: datetime | None
    source_genesis_hash: str
    source_final_event_hash: str
    source_trace_canonical_hash: str | None
    source_result_canonical_hash: str | None
    schema_version: str = "1.0.0"
```

The replay identity is computed solely from this manifest:
$$\text{replay\_id} = \text{"REPLAY-"} + \text{SHA256}(\text{canonical\_json}(\text{manifest}))[:24].\text{upper()}$$
Zero wall-clock time or host metadata enters the identity, guaranteeing strict multi-run determinism.

---

## 5. Event Ordering & Integrity Validation Rules

The replay engine fails closed on any data corruption or tampering:

| Invariant | Violation Detected | Divergence Category |
| :--- | :--- | :--- |
| **Sequence Contiguity** | $S_i \ne S_{i-1} + 1$ | `SEQUENCE_DISCONTINUITY` |
| **Previous Hash Chaining** | $H_{i-1} \ne \text{prev\_hash}_i$ | `HASH_CHAIN_DIVERGENCE` |
| **Logical Event ID** | Computed ID $\ne$ record ID | `EVENT_ID_CORRUPTION` |
| **Canonical Event Hash** | Computed hash $\ne$ record hash | `CANONICAL_HASH_MISMATCH` |
| **Duplicate Event Detection** | Repeated event ID with differing content | `DUPLICATE_EVENT_CONFLICT` |
| **Causal Lineage** | Causation ID not in ancestry set | `BROKEN_CAUSAL_LINEAGE` |
| **Timestamp Monotonicity** | $t_i < t_{i-1}$ | `TIMESTAMP_NON_MONOTONIC` |
| **FSM Transition Legality** | Target state not in allowed transitions | `FSM_ILLEGAL_TRANSITION` |
| **Terminal State Mutability** | Mutating CANCELLED/REJECTED/CLOSED | `FSM_TERMINAL_RESURRECTION` |

---

## 6. State Reconstruction Model

Reconstructed state is encapsulated in `ReplayState`, containing isolated domain state components:
- `orders: dict[str, ReplayOrderState]`: tracks order state, prices, quantities, fill types, and event type histories.
- `position: ReplayPositionState`: single open position tracker (direction, exposure, entry price, fee, slippage).
- `portfolio: ReplayPortfolioState`: exact cash, equity, fees, slippage, and realized PnL.
- `contract: ReplayContractState`: contract identifier, multiplier, expiry timestamp, expired status, and post-expiry fills.
- `risk: ReplayRiskState`: evaluations count, approvals count, rejections count, circuit breaker active status.
- `trace: tuple[TraceEntry, ...]`: replayed execution trace.

### State Fingerprinting
$$\text{state\_fingerprint} = \text{SHA256}(\text{canonical\_json}(\text{state\_dict}))$$
Every state component serializes to canonical JSON with sorted keys and stringified Decimals, ensuring bit-for-bit repeatability.

---

## 7. Checkpoint Creation & Resume Semantics

### Checkpointing
Checkpoints are captured periodically (`event.sequence_number % interval == 0`) and at trade/run completion boundaries (`TRADE_CLOSED`, `BACKTEST_COMPLETED`).
To prevent state bleed across resumed runs, the engine stores deep copies of the state:
```python
cp = ReplayCheckpoint(
    sequence_number=event.sequence_number,
    event_id=event.event_id,
    timestamp=event.event_timestamp,
    state_fingerprint=reconstructed_state.compute_fingerprint(),
    state_snapshot=reconstructed_state.model_copy(deep=True),
)
```

### Resume Equivalence
Resuming from an intermediate checkpoint satisfies the mathematical identity:
$$\text{resume\_from\_checkpoint}(cp_k).\text{final\_state\_fingerprint} \equiv \text{full\_replay}().\text{final\_state\_fingerprint}$$
The engine restores the snapshot, initializes causal history up to sequence $k$, and continues processing from sequence $k+1$ through $N$.

---

## 8. First-Divergence Diagnostics

When an integrity, FSM, trace, or result mismatch occurs, the replay engine logs a structured `ReplayMismatch` record identifying:
```python
class ReplayMismatch(BaseModel):
    divergent_sequence: int
    event_id: str
    event_type: str
    source_fingerprint: str
    replay_fingerprint: str
    source_state_before: str | None
    replay_state_before: str | None
    source_state_after: str | None
    replay_state_after: str | None
    mismatch_category: str
    diagnostic: str
```
This isolates the exact event sequence, before/after states, and rationale for forensic remediation.

---

## 9. Security & Offline Guarantees

1. **`LIVE_TRADING = False` Enforcement**: Replay engine strictly verifies that live trading is disabled.
2. **Zero Network Connectivity**: No HTTP, REST, WebSocket, socket, or IPC calls are permitted or executed.
3. **No Broker Credentials**: No environment variables or credentials for Zerodha, Upstox, Interactive Brokers, or exchange APIs are required or consumed.
4. **Source Immutability**: All source artifacts are ingested in read-only mode (`DeepFreeze` / deep-copied). In-memory or on-disk source ledger objects remain 100% byte-for-byte unchanged.

---

## 10. Test Strategy & Traceability Matrix

### Unit Tests (35 Tests — R1..R34 + Adversarial Golden Test)
| Test ID | Requirement Description | File | Status |
| :--- | :--- | :--- | :--- |
| `R1` | Empty replay controlled warning behavior | `tests/unit/replay/test_replay.py` | PASS |
| `R2` | Single valid event replay | `tests/unit/replay/test_replay.py` | PASS |
| `R3` | Multi-event hash chain verification | `tests/unit/replay/test_replay.py` | PASS |
| `R4` | Tampered event payload rejection | `tests/unit/replay/test_replay.py` | PASS |
| `R5` | Out-of-order sequence rejection | `tests/unit/replay/test_replay.py` | PASS |
| `R6` | Corrupt previous_hash rejection | `tests/unit/replay/test_replay.py` | PASS |
| `R7` | Unknown event type fail-closed | `tests/unit/replay/test_replay.py` | PASS |
| `R8` | Duplicate event ID conflict handling | `tests/unit/replay/test_replay.py` | PASS |
| `R9` | Non-monotonic timestamp rejection | `tests/unit/replay/test_replay.py` | PASS |
| `R10` | Mode A full replay state equivalence | `tests/unit/replay/test_replay.py` | PASS |
| `R11` | Mode B validation-only speed and pass | `tests/unit/replay/test_replay.py` | PASS |
| `R12` | Mode C prefix replay cutoff | `tests/unit/replay/test_replay.py` | PASS |
| `R13` | Checkpoint generation at trade boundary | `tests/unit/replay/test_replay.py` | PASS |
| `R14` | Resume from checkpoint equivalence | `tests/unit/replay/test_replay.py` | PASS |
| `R15` | First-divergence diagnostic format | `tests/unit/replay/test_replay.py` | PASS |
| `R16` | Source ledger immutability | `tests/unit/replay/test_replay.py` | PASS |
| `R17` | Execution trace hash validation | `tests/unit/replay/test_replay.py` | PASS |
| `R18` | Result hash validation | `tests/unit/replay/test_replay.py` | PASS |
| `R19` | Contract expiry replay semantics | `tests/unit/replay/test_replay.py` | PASS |
| `R20` | Full backtest artifact replay pass | `tests/unit/replay/test_replay.py` | PASS |
| `R21` | Source trace mutation without audit-event mutation fails TRACE_HASH_MISMATCH | `tests/unit/replay/test_replay.py` | PASS |
| `R22` | Audit event mutation causes independent replay trace divergence | `tests/unit/replay/test_replay.py` | PASS |
| `R23` | Source BacktestResult hash replaced with false value is detected | `tests/unit/replay/test_replay.py` | PASS |
| `R24` | Source BacktestResult contents mutated while audit events remain unchanged | `tests/unit/replay/test_replay.py` | PASS |
| `R25` | Synthetic/hardcoded trade data cannot appear in replay reconstruction | `tests/unit/replay/test_replay.py` | PASS |
| `R26` | Replay without BacktestResult artifact computes complete trade metrics | `tests/unit/replay/test_replay.py` | PASS |
| `R27` | Execution trace entries generated event-by-event match source trace hash | `tests/unit/replay/test_replay.py` | PASS |
| `R28` | Multi-trade sequence correctly updates cash, portfolio, and closed trades | `tests/unit/replay/test_replay.py` | PASS |
| `R29` | Short trade sequence reconstructs correct PnL and trade metrics | `tests/unit/replay/test_replay.py` | PASS |
| `R30` | Validation-only mode skips trade metric reconstruction | `tests/unit/replay/test_replay.py` | PASS |
| `R31` | Pure audit event replay with no BacktestResult succeeds with NO_SOURCE_RESULT | `tests/unit/replay/test_replay.py` | PASS |
| `R32` | Non-positive exit price fails closed with MISSING_TRADE_DATA | `tests/unit/replay/test_replay.py` | PASS |
| `R33` | Missing open position on exit fill fails closed | `tests/unit/replay/test_replay.py` | PASS |
| `R34` | Terminal state resurrection strictly fails with FSM_TERMINAL_RESURRECTION | `tests/unit/replay/test_replay.py` | PASS |
| `Golden` | Adversarial Golden Test: source audit events valid, reference artifacts wrong | `tests/unit/replay/test_replay.py` | PASS |

### Property Tests (15 Tests — P32..P46)
| Test ID | Invariant Proven | File | Status |
| :--- | :--- | :--- | :--- |
| `P32` | Event Stream Result Hash Determinism | `tests/property/test_replay_properties.py` | PASS |
| `P33` | Event Permutation Rejection | `tests/property/test_replay_properties.py` | PASS |
| `P34` | Event Payload Mutation Detection | `tests/property/test_replay_properties.py` | PASS |
| `P35` | Checkpoint-Resume Equivalence | `tests/property/test_replay_properties.py` | PASS |
| `P36` | Source Immutability | `tests/property/test_replay_properties.py` | PASS |
| `P37` | Contract Expiry Replay & Gate E | `tests/property/test_replay_properties.py` | PASS |
| `P38` | Illegal FSM Sequences Rejected | `tests/property/test_replay_properties.py` | PASS |
| `P39` | Source Trace Mutation Always Detected by Replay Trace Hash | `tests/property/test_replay_properties.py` | PASS |
| `P40` | Replay Trace Strictly Independent of Source Execution Trace | `tests/property/test_replay_properties.py` | PASS |
| `P41` | Result Reconstruction Strictly Reproduces Canonical Result Hash | `tests/property/test_replay_properties.py` | PASS |
| `P42` | Arbitrary BacktestResult Mutations Fail Result Hash Verification | `tests/property/test_replay_properties.py` | PASS |
| `P43` | Full FSM Transition Chain Validity Over Legal Transitions | `tests/property/test_replay_properties.py` | PASS |
| `P44` | Terminal State Resurrection Always Fails with FSM_TERMINAL_RESURRECTION | `tests/property/test_replay_properties.py` | PASS |
| `P45` | Missing Mandatory Trade Attributes Strictly Fail Closed | `tests/property/test_replay_properties.py` | PASS |
| `P46` | State Fingerprint Invariance Across Intermediate Checkpoint Resumption | `tests/property/test_replay_properties.py` | PASS |

---

## 11. Minimal Deterministic Example

```python
from alphaforge.replay import (
    ReplayArtifactSource,
    ReplayConfig,
    ReplayEngine,
    ReplayMode,
    ReplayStatus,
)

# 1. Ingest recorded events and backtest results
source = ReplayArtifactSource(
    events=recorded_events,
    backtest_result=recorded_result,
)

# 2. Configure replay engine
config = ReplayConfig(
    mode=ReplayMode.FULL,
    checkpoint_interval=10,
    verify_audit_hash_chain=True,
    verify_fsm_transitions=True,
    verify_execution_trace=True,
    verify_result_hash=True,
)

# 3. Execute replay
engine = ReplayEngine(config=config, source=source)
result = engine.replay()

# 4. Verify outcome
if result.status == ReplayStatus.PASS:
    print(f"Replay verified successfully: {result.replay_id}")
    print(f"Final State Fingerprint: {result.final_state_fingerprint}")
else:
    print(f"Replay failed at sequence: {result.first_divergence.divergent_sequence}")
    print(f"Diagnostic: {result.first_divergence.diagnostic}")
```
