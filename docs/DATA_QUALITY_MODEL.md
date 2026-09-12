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

## 3. Execution Eligibility Contract

AlphaForge enforces an explicit, fail-closed execution eligibility contract via `NormalizationResult.execution_allowed`:

```python
EXECUTION_BLOCKING_STATUSES: frozenset[DataQualityStatus] = frozenset(
    {
        DataQualityStatus.INVALID,
        DataQualityStatus.CONFLICT,
        DataQualityStatus.GAP,
        DataQualityStatus.STALE,
        DataQualityStatus.INCOMPLETE,
    }
)

RECOVERABLE_STATUSES: frozenset[DataQualityStatus] = frozenset(
    {
        DataQualityStatus.VALID,
        DataQualityStatus.DUPLICATE,
        DataQualityStatus.OUT_OF_ORDER,
    }
)


def is_execution_eligible(status: DataQualityStatus) -> bool:
    return status in RECOVERABLE_STATUSES
```

### Execution-Blocking Statuses:
- **`INVALID`:** Fatal mathematical or boundary breach $\implies$ `execution_allowed = False`.
- **`CONFLICT`:** Divergent values for identical timestamp $\implies$ `execution_allowed = False`.
- **`GAP`:** Missing candle interval $\implies$ `execution_allowed = False`.
- **`STALE`:** Data age exceeds maximum staleness $\implies$ `execution_allowed = False`.
- **`INCOMPLETE`:** History count below minimum indicator warm-up $\implies$ `execution_allowed = False`.
- **`EMPTY`:** Zero input candles available $\implies$ `execution_allowed = False`.

### Recoverable Statuses (Duplicate & Out-Of-Order Exception):
- **`DUPLICATE`:** Exact bit-for-bit duplicate records are idempotently deduplicated. Once deduplicated, the remaining series is clean and **execution-eligible** (`execution_allowed = True`). Duplicate status is explicitly **NOT** an execution blocker after deterministic deduplication.
- **`OUT_OF_ORDER`:** Records received out of sequence are chronologically sorted via deterministic secondary sorting. Once sorted, the continuous series is **execution-eligible** (`execution_allowed = True`).
- **`VALID`:** Uncompromised continuous series $\implies$ `execution_allowed = True`.

---

## 4. No Synthetic Candle Policy & Real Forming Candle Requirement

AlphaForge strictly prohibits candle fabrication:
1. **No Synthetic Candles:** The system will **never fabricate a market candle**, **never forward-fill OHLC prices**, and **never create zero-volume placeholder candles**.
2. **Real Forming Candle Requirement:** The Phase 1 strategy execution input contract requires slot `[0]` to contain a real forming candle (`is_closed=False`):
   - If a real forming candle exists in store or is explicitly supplied $\implies$ placed at index `[0]`.
   - If no real forming candle exists $\implies$ `CandleStore.get_strategy_execution_input()` returns an explicit unavailable state (`None`).
   - Slot `[0]` is **never satisfied using fabricated data**.
3. **Execution Gate:** Downstream strategy execution is immediately blocked (`None` returned) if:
   - No real forming candle is present.
   - Any candle in the execution window possesses an execution-blocking status.
   - Insufficient closed history is available.
