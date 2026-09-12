# ALPHAFORGE — STRATEGY DETERMINISM & HASHING SPECIFICATION

**Project Name:** AlphaForge  
**Document Type:** Technical Determinism Specification  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Author:** Principal Quantitative Systems Engineer  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan & Phase 0 Requirement Freeze

---

## 1. Mathematical Determinism Proof

The Strategy Engine guarantees that for any two executions $E_1$ and $E_2$:
$$\text{If } \mathcal{I}_1 \equiv \mathcal{I}_2 \implies \Sigma_1 \equiv \Sigma_2$$
Where $\mathcal{I}$ represents the complete input tuple $(S_{exec}, S_{conf}, C_{fut}, \Theta_{cfg}, T_{eval})$ and $\Sigma$ represents the output `StrategySignal`.

### 1.1 Sources of Non-Determinism & Architectural Countermeasures

| Potential Hazard | Failure Mechanism | AlphaForge Architectural Countermeasure |
| :--- | :--- | :--- |
| **System Wall-Clock Leakage** | Calling `datetime.now()` causes decisions to vary based on execution time. | Strict ban on system time. All timestamps are passed explicitly via market candle headers. |
| **Random Number Generators** | Using `random` or stochastic algorithms produces unpredictable signals. | Pure deterministic mathematics. Zero random libraries imported in core packages. |
| **Floating-Point Imprecision** | IEEE 754 float rounding differs across CPU architectures (x86 vs ARM). | All prices, stops, targets, and risk distances are computed using Python `Decimal` with fixed 4 decimal precision. |
| **Forming Candle Mutation** | Evaluating candle `[0]` causes signals to jitter with incoming intra-bar ticks. | Hard architectural quarantine: strategy engine slices `candles[1:]`, discarding `[0]`. |
| **Unordered Dict Serialization** | Non-deterministic JSON key order alters configuration hashes. | Canonical serialization: JSON keys sorted alphabetically (`sort_keys=True`) with compact separators (`separators=(',', ':')`). |
| **Global Mutable State** | Static class variables retain state across invocations. | Strategy engine is stateless; configuration and history are passed explicitly as immutable arguments. |

---

## 2. Canonical Configuration Hashing (`config_hash`)

The strategy configuration $\Theta_{cfg}$ is hashed into a unique fingerprint:

```python
import hashlib
import json


def compute_config_hash(config_dict: dict) -> str:
    canonical_json = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

- Any modification to lookback periods, thresholds, multipliers, or symbol targets immediately changes the `config_hash`.
- The `config_hash` is stamped on every generated `StrategySignal`.

---

## 3. Deterministic Signal ID Formulation (`signal_id`)

A signal identifier must be uniquely reproducible during historical replay and live execution:

```python
def compute_signal_id(
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    direction: str,
    signal_timestamp_iso: str,
    config_hash: str,
) -> str:
    payload = f"{strategy_id}:{strategy_version}:{symbol}:{direction}:{signal_timestamp_iso}:{config_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
```

- **Invariant:** Identical inputs across separate runs produce the exact same 24-character hex `signal_id`.
- **Prohibitions:** No UUID4, no microsecond timestamps, no PID, no thread ID.
