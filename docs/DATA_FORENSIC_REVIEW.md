# ALPHAFORGE — DATA FORENSIC SPECIFICATION & GOVERNANCE

**Project Name:** AlphaForge  
**Author:** Principal Data Engineer, Reliability Engineer & Auditor  
**Date:** 2026-09-12  
**Status:** Approved Data Governance Specification  
**Baseline Reference:** AlphaForge Master Constitution (Section 9)

---

## 1. Data Safety Philosophy: Fail-Closed Ingestion

In automated trading systems, corrupted, delayed, or missing data directly leads to erroneous orders and capital destruction. AlphaForge implements an uncompromising **Fail-Closed Data Pipeline**:
- If data quality cannot be guaranteed with 100% mathematical certainty, trading must cease immediately.
- Never guess missing values. Never perform silent linear interpolation on missing candles.
- Duplicate ticks or candles must never silently overwrite existing validated state.

---

## 2. Canonical Market Data Schema

Every candle and tick ingested into AlphaForge must strictly adhere to the immutable `MarketCandle` data contract:

```python
class MarketCandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    symbol: str  # e.g., "NIFTY"
    instrument_type: str  # "INDEX" | "FUTURES" | "EQUITY"
    contract_id: str  # e.g., "NIFTY26SEP25000FUT" or "INDEX_SPOT"
    exchange_timestamp: datetime  # Timestamp assigned by the exchange matching engine
    received_timestamp: datetime  # Timestamp when packet reached AlphaForge gateway
    timeframe: str  # e.g., "1m", "3m", "5m", "15m", "1d"
    open: Decimal  # Strict fixed-point Decimal representation
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int  # Traded volume in contracts/shares
    open_interest: int | None  # Open interest (mandatory for futures, None for index)
    source: str  # e.g., "ZERODHA_WS", "ANGEL_REST", "PARQUET_HISTORICAL"
    quality_status: str  # "VALIDATED" | "SUSPECT" | "CORRUPTED"
    data_version: int  # Schema revision tracking
```

---

## 3. Mandatory Mathematical Invariants

Before any candle is permitted to enter the Candle Store or Indicator Engine, it must pass the following structural assertions:

1. **OHLC Price Boundary Integrity:**
   $$\text{high} \ge \max(\text{open}, \text{close}, \text{low})$$
   $$\text{low} \le \min(\text{open}, \text{close}, \text{high})$$
   $$\text{open} > 0, \quad \text{high} > 0, \quad \text{low} > 0, \quad \text{close} > 0$$

2. **Volume & Open Interest Non-Negativity:**
   $$\text{volume} \ge 0$$
   $$\text{open\_interest} \ge 0 \quad (\text{when instrument\_type} == \text{"FUTURES"})$$

3. **Strict Monotonicity:**
   For any consecutive candle sequence $C_k, C_{k+1}$ on identical `(symbol, contract_id, timeframe)`:
   $$\text{exchange\_timestamp}(C_{k+1}) > \text{exchange\_timestamp}(C_k)$$

4. **Zero Duplicate Identity:**
   The composite key `(symbol, contract_id, timeframe, exchange_timestamp)` is globally unique. Any incoming candle with an identical key must trigger a collision alert. If the incoming payload is identical, it is discarded as a duplicate network packet; if it differs in price/volume, it is flagged as `DATA_ANOMALY_COLLISION` and quarantined.

---

## 4. Anomaly Detection & Gap Handling Policy

When a gap or missing candle is detected (e.g. expected 3-minute interval skips from 10:00 to 10:06 without a 10:03 candle):

```text
[ GAP DETECTED IN STREAM ]
            |
            v
1. STOP SIGNAL GENERATION IMMEDIATELY
            |
            v
2. EMIT AUDIT ALERT: "DATA_GAP_DETECTED"
            |
            v
3. LOCK EXECUTION ENGINE (Set trading_state = BLOCKED_DATA_GAP)
            |
            v
4. TRIGGER AUTOMATED HISTORICAL BACKFILL via REST API
            |
            v
5. RE-VALIDATE COMPLETE CANDLE SEQUENCE
            |
            v
6. RESUME TRADING ONLY AFTER VERIFICATION PASSES
```

### 4.1 Stale Data Thresholds
- For live WebSocket feeds, if no tick or candle update is received within $T_{stale} = 15\text{ seconds}$ during market hours:
  - System flags `FEED_STALE`.
  - Strategy halts with `REJECT_DATA_STALE`.
  - No new orders are permitted until feed heartbeat returns to nominal latency.
