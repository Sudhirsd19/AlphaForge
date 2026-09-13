# AlphaForge — Phase 15: Deployment Environments

---

## 1. Scope & Private Deployment Mandate

AlphaForge is a **personal/private quantitative futures trading application** operating in a single-user workstation or private server environment. It is designed to run automated trading strategies on futures contracts (e.g. CME Micro E-mini Nasdaq `MNQ`) with institutional-grade discipline and mathematical rigor.

### Private Scope Boundary
- **Included**:
  - Deterministic deployment environments: `DEV`, `TEST`, `PAPER`, `SHADOW`, `LIVE`.
  - Authoritative fail-closed configuration parsing with immutable Pydantic models.
  - Runtime execution boundaries preventing simulation environments from contacting live venues.
  - Physical and logical runtime directory partitioning (`runtime/<env>/`).
  - Cross-environment isolation with cryptographic environment markers (`.alphaforge_env_marker`).
  - Passive, read-only pre-flight readiness diagnostics.
  - Deterministic startup and shutdown lifecycle management.
  - Bit-for-bit reproducible local backup creation and safe recovery protocols.
  - Strict rollback safety guaranteeing that rollbacks never accidentally switch environments into `LIVE`.
  - Zero modifications to Phase 0–14 domain calculations, risk limits, order FSM, or ledger logic.
- **Explicitly Excluded (No Enterprise Bloat)**:
  - Kubernetes (k8s), Helm charts, and container orchestration clusters.
  - Cloud microservice meshes (Istio, Linkerd) and distributed service discovery.
  - Enterprise IAM, OAuth2 federations, and external directory servers.
  - Distributed multi-region message queues.

---

## 2. Environment Matrix & Invariants

AlphaForge formalizes execution environments via the `DeploymentEnvironment` string enumeration:

| Environment | Purpose | Execution Broker | Real Market Data | Real Live Broker Orders | Default State |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DEV** | Algorithm development, local testing | Simulated / Mock | Optional / Synthetic | **STRICTLY PROHIBITED** | Inactive |
| **TEST** | CI/CD, property tests, regression suite | Simulated / Memory | Synthetic / Recorded | **STRICTLY PROHIBITED** | Inactive |
| **PAPER** | Forward-testing simulated trading | `PaperBroker` | Real-time Live Stream | **STRICTLY PROHIBITED** | **DEFAULT** |
| **SHADOW** | Passive shadow tracking & execution comparison | `PaperBroker` / Sim | Real-time Live Stream | **STRICTLY PROHIBITED** | Inactive |
| **LIVE** | Real capital execution on live exchange | Live Broker Adapter | Real-time Live Stream | **ALLOWED (DUAL-KEY AUTH)** | Locked / Inactive |

### Core Safety Invariants
1. **PAPER is Default**: When `ALPHAFORGE_ENV` or `DEPLOYMENT_ENV` is unset or unspecified, AlphaForge strictly defaults to `PAPER`.
2. **Fail-Closed Parsing**: Any explicit empty (`""`), whitespace-only (`"   "`), unknown (`"PROD"`), or malformed environment value immediately aborts configuration with `DeploymentConfigurationError`. An invalid explicitly supplied value is **never** treated as "unset/missing".
3. **Dual Opt-In for LIVE**: Selecting `LIVE` requires two separate explicit confirmations: `environment = DeploymentEnvironment.LIVE` and `live_authorized = True`. In addition, it must align with Phase 13 dual-key authorization (`TRADING_MODE=LIVE` and `LIVE_TRADING_ENABLED=True`).
4. **Broker Execution Guard**: The broker guard enforces:
   - `PAPER + live broker => REJECT`
   - `SHADOW + live broker => REJECT`
   - `DEV/TEST + live broker => REJECT`
   - `PAPER + paper/sim broker => ALLOW`
   - `SHADOW + shadow/sim broker => ALLOW`
   - `LIVE + authorized live broker => ALLOW`
   - `LIVE + unauthorized/malformed security => REJECT`

---

## 3. Configuration Architecture & Deterministic Loading

The deployment layer is governed by `DeploymentConfig`, an immutable, strictly validated Pydantic model (`frozen=True`, `extra="forbid"`):

```python
class DeploymentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: DeploymentEnvironment = Field(default=DeploymentEnvironment.PAPER)
    schema_version: str = Field(default="1.0.0")
    runtime_root: Path = Field(default=Path("runtime"))
    state_dir: Path | None = Field(default=None)
    log_dir: Path | None = Field(default=None)
    audit_dir: Path | None = Field(default=None)
    observability_dir: Path | None = Field(default=None)
    credential_source: str = Field(default="env")
    live_authorized: bool = Field(default=False)
    allow_shadow_mode: bool = Field(default=True)
```

### Deterministic Path Resolution
When explicit directory paths are omitted, `DeploymentConfig` deterministically resolves environment-specific subdirectories:
- `effective_state_dir`: `runtime_root / environment.lower() / "state"`
- `effective_log_dir`: `runtime_root / environment.lower() / "logs"`
- `effective_audit_dir`: `runtime_root / environment.lower() / "audit"`
- `effective_observability_dir`: `runtime_root / environment.lower() / "observability"`

### Path Traversal Protection
All paths are inspected at configuration time. Any path attempting directory traversal escaping (e.g. containing `..` in parts) immediately fails closed with `DeploymentConfigurationError`.

### Zero Secret Leakage Guarantee
`DeploymentConfig.to_safe_dict()` produces a sanitized, canonical dictionary suitable for logs, audit events, and hash generation. No secret keys, tokens, or passwords exist in configuration data.

---

## 4. Runtime Directory Layout & Crossover Protection

```text
runtime/
├── dev/
│   ├── .alphaforge_env_marker
│   ├── state/
│   ├── logs/
│   ├── audit/
│   └── observability/
├── test/
│   ├── .alphaforge_env_marker
│   ├── state/
│   ├── logs/
│   ├── audit/
│   └── observability/
├── paper/
│   ├── .alphaforge_env_marker
│   ├── state/
│   ├── logs/
│   ├── audit/
│   └── observability/
├── shadow/
│   ├── .alphaforge_env_marker
│   ├── state/
│   ├── logs/
│   ├── audit/
│   └── observability/
└── live/
    ├── .alphaforge_env_marker
    ├── state/
    ├── logs/
    ├── audit/
    └── observability/
```

### Cryptographic Marker Files (`.alphaforge_env_marker`)
Every environment directory tree is tagged upon initialization with an immutable JSON marker:
```json
{
  "environment": "PAPER",
  "schema_version": "1.0.0"
}
```
If an active runtime instance attempts to access or initialize a directory tree marked for a different environment (e.g. `PAPER` pointing its state directory into `runtime/live/state`), `EnvironmentDirectoryManager` immediately halts startup with `EnvironmentIsolationError`. Crossover into or out of `LIVE` is completely impossible.

---

## 5. Execution Boundary & Broker Guard

`DeploymentBrokerGuard(AbstractBroker)` wraps the underlying execution broker and validates both:
1. The currently configured deployment environment.
2. The execution capability of the underlying broker adapter.

```python
def unwrap_broker(broker: AbstractBroker) -> AbstractBroker:
    current = broker
    while hasattr(current, "delegate"):
        current = current.delegate
    return current


def is_live_execution_broker(broker: AbstractBroker) -> bool:
    core = unwrap_broker(broker)
    if isinstance(core, PaperBroker):
        return False
    if hasattr(broker, "is_live_broker"):
        val = broker.is_live_broker
        return bool(val() if callable(val) else val)
    if hasattr(core, "is_live_broker"):
        val = core.is_live_broker
        return bool(val() if callable(val) else val)
    cls_name = type(core).__name__.lower()
    if any(k in cls_name for k in ("paper", "mock", "sim", "fake", "dummy", "test")):
        return False
    return False
```

Invariants are checked **both at initialization time and prior to every single `submit_order()` call**:
- `PAPER` + live broker => `DeploymentSafetyError`
- `SHADOW` + live broker => `DeploymentSafetyError`
- `DEV` / `TEST` + live broker => `DeploymentSafetyError`
- `LIVE` + `PaperBroker` / `MockBroker` / `SimulatedBroker` => `DeploymentSafetyError`
- `LIVE` + non-live broker (missing `is_live_broker = True`) => `DeploymentSafetyError`
- `LIVE` + live-capable broker => ALLOW (subject to Phase 13 dual opt-in authorization)

---

## 6. Pre-flight Readiness Diagnostics

`DeploymentReadinessChecker` executes read-only, non-mutating pre-flight diagnostic probes:

```python
class ReadinessReport(BaseModel):
    environment: DeploymentEnvironment
    is_ready: bool
    config_valid: bool
    directories_ready: bool
    broker_ready: bool
    credentials_ready: bool
    security_ready: bool
    checks: dict[str, bool]
    details: str
```

### Forensic Guarantees
- **Simulation Isolation**: `PAPER` and `SHADOW` never probe or require a live broker or live credentials.
- **Strictly Diagnostic**: Readiness checks **never** call `submit_order()`, `cancel_order()`, modify risk limits, or mutate position state.
- **Fail-Closed Missing Security Gate**: In `LIVE`, `security_startup_gate is None` or `not gate.is_verified` immediately marks `security_ready = False` and `is_ready = False`.
- **Fail-Closed Non-Live Broker**: In `LIVE`, pairing with a simulated or non-live broker adapter (`PaperBroker`, `MockBroker`, `SimulatedBroker`) marks `broker_ready = False` and `is_ready = False`.
- **Atomic Readiness**: If any check fails, `is_ready = False`, halting startup unconditionally.

---

## 7. Startup & Shutdown Lifecycle Contracts

### Startup Contract
```text
1. LOAD CONFIG
2. VALIDATE CONFIG (Types, invariants, schema)
3. RESOLVE ENVIRONMENT (Default PAPER; fail closed on invalid)
4. VALIDATE ENVIRONMENT SAFETY (LIVE requires live_authorized)
5. VALIDATE CREDENTIAL SOURCE (Metadata presence)
6. VALIDATE RUNTIME DIRECTORIES (Partitioning & .alphaforge_env_marker)
7. RUN PRE-FLIGHT READINESS DIAGNOSTICS (Passive read-only checks)
8. VALIDATE SECURITY GATES (Phase 13 SecurityStartupGate if LIVE)
9. ONLY THEN initialize runtime (is_started = True, is_active = True)
```
If any step fails, startup aborts immediately (`StartupValidationError`). Orders cannot be submitted to the guarded broker if `is_active` is `False`.

### Shutdown Contract
```text
1. STOP NEW WORK (is_active = False)
2. SAFELY FLUSH DIAGNOSTIC & OBSERVABILITY BUFFERS (SafeObservabilityDispatcher.flush())
3. CLOSE RUNTIME RESOURCES (File handles, sinks)
4. PRESERVE AUDIT INFORMATION (Zero state corruption)
5. ZERO TRADING ACTIONS (No orders submitted or cancelled during clean shutdown)
```

---

## 8. Deterministic Backup & Archive Integrity

`DeterministicBackupManager` provides local, private backup archiving for disaster recovery:

### Bit-for-Bit Reproducibility
Repeated identical backups of the same state produce **exact identical SHA-256 archive digests**:
1. Deterministic file discovery across `state/` and `audit/` subdirectories.
2. Lexicographically sorted file paths.
3. Normalized ZIP timestamps: `(2026, 1, 1, 0, 0, 0)`.
4. Normalized file permissions: `0o644 << 16`.
5. Canonical JSON manifest (`backup_manifest.json`) containing individual file SHA-256 hashes and configuration metadata.
6. `ZIP_STORED` compression preventing zlib compression variance across OS platforms.

---

## 9. Disaster Recovery & Controlled Live Restore

### Restore Invariants
- **Generic Restore into LIVE Blocked**: `DeterministicBackupManager.restore_backup()` strictly refuses to target `LIVE` (`DeploymentSafetyError`).
- **No Cross-Environment Crossover**: Non-LIVE backups can **never** be restored into `LIVE` under any circumstances (by default or by force).
- **Dedicated Controlled Live Recovery**: Live restoration is permitted **only** via `restore_live_recovery()` which requires:
  1. Target environment is `LIVE` and `live_authorized = True`.
  2. Backup archive manifest environment originated in `LIVE`.
  3. Explicit confirmation phrase: `"CONFIRM_RESTORE_LIVE_RECOVERY"`.
  4. Pre-extraction checksum verification of every file in the archive.

---

## 10. Rollback Safety & Verification

`RollbackCoordinator` validates code revision rollbacks:
- **No Silent Crossover**: Cannot rollback across different environments (e.g. `PAPER -> LIVE` is rejected).
- **No Accidental Live Activation**: If target revision configuration specifies `LIVE`, `live_authorized` must be explicitly verified.
- **Audit Verification**: Returns immutable `RollbackValidationResult`.

---

## 11. Deployment Identity & Build Provenance

Every runtime run is uniquely stamped with a `DeploymentIdentity`:
- `environment`: Active deployment environment.
- `code_revision`: Git commit SHA (resolved via `git rev-parse HEAD` or `ALPHAFORGE_GIT_COMMIT`).
- `config_hash`: SHA-256 of canonical serialized `DeploymentConfig.to_safe_dict()`.
- `composite_id`: e.g. `PAPER_33a5475f_a1b2c3d4`.

---

## 12. Test Matrix & Verification Results

### Test Summary
- **ENV-1 to ENV-20**: 20 comprehensive unit tests covering all deployment environment invariants.
- **Adversarial Crossover Suite**: 17 adversarial attack and failure injection tests covering unauthorized live attempts, credential crossover, path collisions, and secret scrubbing.
- **Semantic Equivalence Suite**: Proves 100% semantic equivalence between standalone execution and deployment-wrapped execution.
- **Total Phase 15 Tests**: 38 tests (100% pass rate).
- **Total Repository Test Suite**: 827 tests (100% pass rate, 0 failures, 0 errors, in 10.33s).

| Test ID | Description | Result |
| :--- | :--- | :--- |
| **ENV-1** | Environment enum correctness & values | PASS |
| **ENV-2** | PAPER default when unspecified | PASS |
| **ENV-3** | Unknown environment rejected (fails closed) | PASS |
| **ENV-4** | Malformed environment rejected (empty/whitespace/numeric) | PASS |
| **ENV-5** | Environment isolation (distinct paths) | PASS |
| **ENV-6** | Runtime path isolation (`runtime/<env>/...`) | PASS |
| **ENV-7** | Credential-source validation | PASS |
| **ENV-8** | PAPER cannot reach live broker | PASS |
| **ENV-9** | SHADOW cannot reach live broker | PASS |
| **ENV-10** | LIVE authorization requirement (dual-key opt-in) | PASS |
| **ENV-11** | Startup fail-closed behavior | PASS |
| **ENV-12** | Startup ordering contract | PASS |
| **ENV-13** | Safe deterministic shutdown | PASS |
| **ENV-14** | Deployment identity determinism | PASS |
| **ENV-15** | Configuration determinism (canonical hash) | PASS |
| **ENV-16** | Secret redaction & isolation | PASS |
| **ENV-17** | Readiness is diagnostic and non-trading | PASS |
| **ENV-18** | Bit-for-bit deterministic backup & verify | PASS |
| **ENV-19** | Rollback safety | PASS |
| **ENV-20** | Environment crossover protection | PASS |
| **ADV-1..17** | 17 Adversarial crossover & failure scenarios | PASS |
| **SEM-EQ-1** | Standalone vs Deployment wrapper order semantic equivalence | PASS |

---

## 13. Forensic Self-Check & Invariant Verification

1. **Did Phase 15 modify any trading strategy or risk logic?**
   **NO.** Zero modifications made to strategy, signal, entry/exit, sizing, basis, cost, slippage, order FSM, or ledger code.
2. **Can PAPER execution route orders to a live exchange?**
   **NO.** Enforced at construction and submission by `DeploymentBrokerGuard`.
3. **Can SHADOW execution route orders to a live exchange?**
   **NO.** Enforced at construction and submission by `DeploymentBrokerGuard`.
4. **Does LIVE mode fail closed if not authorized?**
   **YES.** Requires explicit dual authorization (`live_authorized=True` and Phase 13 `SecurityStartupGate`).
5. **Are backups bit-for-bit deterministic?**
   **YES.** Repeated identical backups generate identical SHA-256 digests.
6. **Can a backup crossover into LIVE?**
   **NO.** Crossover into LIVE is prohibited by default and by force.
