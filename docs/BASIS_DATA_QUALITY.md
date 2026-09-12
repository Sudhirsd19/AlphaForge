# AlphaForge ? Phase 4: Basis Data Quality & Validation Rules

---

## 1. Overview

The **Basis Data Quality Model** bridges the Phase 2 Market Data Governance Layer and Phase 3 Contract Lifecycle Engine into the Phase 4 Basis Engine. The basis engine enforces a strict fail-closed contract: any defect, anomaly, or ambiguity in either the spot or futures input stream prevents basis computation and marks the observation invalid.

```mermaid
graph TD
    RawCandles[Index & Futures Candles] --> QGate{Phase 2 Quality Status}
    QGate -->|INVALID / INCOMPLETE| R_INV[BasisStatus.INVALID]
    QGate -->|CONFLICT| R_CONF[BasisStatus.DATA_CONFLICT]
    QGate -->|GAP| R_GAP[BasisStatus.DATA_GAP]
    QGate -->|STALE| R_STALE[BasisStatus.STALE]
    QGate -->|VALID / Clean| TimeGate{Temporal Validity}

    TimeGate -->|Future Dated| R_FUT[BasisStatus.FUTURE_DATED_DATA]
    TimeGate -->|Age > 300s| R_STALE
    TimeGate -->|Skew > 60s| R_SKEW[BasisStatus.MISALIGNED_TIMESTAMP]
    TimeGate -->|In-Bounds| LifeGate{Contract Lifecycle}

    LifeGate -->|Declared INVALID/UNKNOWN| R_CINV[BasisStatus.CONTRACT_INVALID]
    LifeGate -->|Suspended| R_CSUSP[BasisStatus.CONTRACT_SUSPENDED]
    LifeGate -->|Not Yet Listed| R_CNLIST[BasisStatus.CONTRACT_NOT_LISTED]
    LifeGate -->|Expired| R_CEXP[BasisStatus.CONTRACT_EXPIRED]
    LifeGate -->|ACTIVE / EXPIRING| Pass[BasisStatus.VALID]
```

---

## 2. Phase 2 Data Quality Mapping Matrix

The Basis Engine inspects the `DataQualityStatus` of both the spot index candle and the futures candle. If either candle has an execution-blocking status, the observation fails closed according to the following precedence matrix:

| Input Data Quality Status | Basis Engine Status (`BasisStatus`) | Confirmation Status | Rationale |
| :--- | :--- | :--- | :--- |
| `VALID` | Evaluates next gates | Depends on lifecycle & stats | Clean candle data conforms to mathematical OHLC requirements. |
| `INVALID` | `INVALID` | `INVALID` | Mathematical inconsistency or schema violation. |
| `CONFLICT` | `DATA_CONFLICT` | `INVALID` | Divergent tick data or multiple non-identical candles for same timestamp. |
| `GAP` | `DATA_GAP` | `INVALID` | Missing expected bar in interval sequence. |
| `STALE` | `STALE` | `INVALID` | Zero volume or repeated flat pricing across expected active intervals. |
| `INCOMPLETE` | `INVALID` | `INVALID` | Incomplete bar missing OHLCV constituents. |
| `OUT_OF_ORDER` | Normalized / Recovered | Continues if normalized | Out-of-order bars are deterministically re-sorted by Phase 2 normalizer. |
| `DUPLICATE` | Normalized / Recovered | Continues if normalized | Identical duplicate bars are deduplicated by Phase 2 normalizer. |

---

## 3. Temporal Governance & Timestamp Alignment

### 3.1 Future-Dated Data Protection (Causality Gate)
To prevent look-ahead bias and simulated timestamps leaked from future bars:
$$\text{If } \tau_{\text{index}} > \tau_{\text{eval}} \quad \text{or} \quad \tau_{\text{futures}} > \tau_{\text{eval}} \implies \mathbf{BasisStatus.FUTURE\_DATED\_DATA}$$

### 3.2 Maximum Staleness Gate
To ensure the basis observation reflects current market conditions:
$$\text{If } (\tau_{\text{eval}} - \tau_{\text{index}}) > 300\text{s} \quad \text{or} \quad (\tau_{\text{eval}} - \tau_{\text{futures}}) > 300\text{s} \implies \mathbf{BasisStatus.STALE}$$

### 3.3 Timestamp Skew & Alignment Gate
In high-frequency and multi-asset environments, index and futures bars may close with slight clock skew. A maximum skew corridor of 60 seconds is permitted:
$$\Delta t = |\tau_{\text{futures}} - \tau_{\text{index}}|$$
$$\text{If } \Delta t > 60\text{s} \implies \mathbf{BasisStatus.MISALIGNED\_TIMESTAMP}$$

---

## 4. Instrument & Contract Lifecycle Governance

### 4.1 Underlying Symbol Consistency
The spot index and derivative futures contract must share the exact underlying instrument:
$$\text{If } \text{symbol}_{\text{index}} \ne \text{contract.underlying\_symbol} \quad \text{or} \quad \text{symbol}_{\text{futures}} \ne \text{contract.underlying\_symbol} \implies \mathbf{BasisStatus.UNDERLYING\_MISMATCH}$$

### 4.2 Contract Identity Verification
The futures candle `contract_id` must match the registered `ContractMaster.contract_id`:
$$\text{If } \text{candle}_{\text{futures}}.\text{contract\_id} \ne \text{contract}.\text{contract\_id} \implies \mathbf{BasisStatus.UNDERLYING\_MISMATCH}$$

### 4.3 Phase 3 Lifecycle State Integration
The futures contract is evaluated dynamically via `evaluate_contract_lifecycle(contract, eval_ts)`:
- **`ContractStatus.INVALID` or `ContractStatus.UNKNOWN`:** Evaluates to `BasisStatus.CONTRACT_INVALID`.
- **`ContractStatus.SUSPENDED` or `contract.is_suspended == True`:** Evaluates to `BasisStatus.CONTRACT_SUSPENDED`.
- **`ContractStatus.NOT_YET_LISTED`:** Evaluates to `BasisStatus.CONTRACT_NOT_LISTED`.
- **`ContractStatus.EXPIRED`:** Evaluates to `BasisStatus.CONTRACT_EXPIRED`.
- **`ContractStatus.ACTIVE` or `ContractStatus.EXPIRING`:** Passes lifecycle gate.

---

## 5. Price Sanity & Numerical Finiteness

Both `index_candle.close` and `futures_candle.close` must strictly be positive finite `Decimal` values:
- Non-positive prices ($P \le 0$) immediately trigger `BasisStatus.INVALID`.
- Non-finite numbers (`NaN`, `Infinity`, `-Infinity`) trigger `BasisStatus.INVALID`.
- Silent rounding or float truncation is strictly forbidden.
