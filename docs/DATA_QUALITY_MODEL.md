# AlphaForge — Data Quality Model Specification

---

## 1. Quality Precedence Hierarchy

When evaluating market data batches, multiple anomalies or quality defects may occur concurrently. AlphaForge enforces an explicit, deterministic precedence hierarchy:

$$\mathbf{INVALID} > \mathbf{CONFLICT} > \mathbf{OUT\_OF\_ORDER} > \mathbf{DUPLICATE} > \mathbf{GAP} > \mathbf{STALE} > \mathbf{INCOMPLETE} > \mathbf{EMPTY} > \mathbf{VALID}$$

The formal rank values (where Rank 1 is the most severe defect):

| Rank | Status Enum Member | Severity Level | Fail-Closed Policy | Description |
| :---: | :--- | :---: | :---: | :--- |
| **1** | `INVALID` | Fatal Defect | Immediate Quarantine | Mathematical boundary breach ($H < L$, negative prices, NaN, negative volume, bad boundary). |
| **2** | `CONFLICT` | Fatal Defect | Immediate Quarantine | Divergent OHLCV records for identical composite key `(symbol, timeframe, timestamp)`. |
| **3** | `OUT_OF_ORDER` | Recoverable | Audit & Sort | Records received non-chronologically. Sorted during normalization; flagged for audit. |
| **4** | `DUPLICATE` | Benign / Deduped | Deduplicate | Bit-for-bit identical duplicate records. Safely collapsed into single record. |
| **5** | `GAP` | Execution Blocker | Fail-Closed Reject | Missing expected candle interval (e.g. 09:18 to 09:24 on 3m bar). Zero synthetic imputation. |
| **6** | `STALE` | Execution Blocker | Fail-Closed Reject | Latest candle age exceeds `max_stale_seconds` (195s) relative to evaluation context. |
| **7** | `INCOMPLETE` | Execution Blocker | Fail-Closed Reject | Available historical candle count below strategy minimum indicator warm-up requirement. |
| **8** | `EMPTY` | Informational | Zero-Signal Reject | Zero records provided to ingestion pipeline. |
| **9** | `VALID` | Normal Operation | Allow Execution | Fully verified, continuous, fresh, closed candle series ready for evaluation. |

---

## 2. Multi-Defect Resolution Rule

The normalizer evaluates batches holistically:
```python
def most_severe_status(statuses: Iterable[DataQualityStatus]) -> DataQualityStatus:
    status_list = list(statuses)
    if not status_list:
        return DataQualityStatus.EMPTY
    return min(status_list, key=lambda s: QUALITY_PRECEDENCE_RANK[s])
```

If a batch contains both an `OUT_OF_ORDER` sequence and a `CONFLICT` record, the resulting batch classification is `CONFLICT` (Rank 2 > Rank 3).

If a batch contains both a `DUPLICATE` record and a `GAP`, the resulting classification is `DUPLICATE` (Rank 4 > Rank 5).

If a batch has no defects, its classification is `VALID`.

---

## 3. Downstream Strategy Integration

The Strategy Engine in Phase 1 accepts `StrategySignal` decisions:
- Batches classified with status $\ne \text{VALID}$ trigger appropriate rejection decisions:
  - `INVALID` $\to$ `REJECT_DATA_INVALID`
  - `STALE` $\to$ `REJECT_DATA_STALE`
  - `INCOMPLETE` $\to$ `REJECT_INSUFFICIENT_HISTORY`
  - `GAP` $\to$ `REJECT_DATA_INVALID` (or fail-closed rejection)

This ensures complete alignment across the data governance layer and execution safety.
