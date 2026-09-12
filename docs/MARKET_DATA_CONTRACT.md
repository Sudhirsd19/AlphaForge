# AlphaForge — Market Data Contract Specification

---

## 1. Canonical Schema (`MarketCandle`)

The canonical market candle schema implements all 15 mandatory fields specified in Master Specification Section 9:

| Field Index | Field Name | Data Type | Precision / Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| **1** | `symbol` | `str` | Uppercase non-empty ASCII string | Normalized asset ticker (e.g. `"NIFTY"`) |
| **2** | `instrument_type` | `InstrumentType` | `INDEX`, `FUTURES`, `EQUITY` | Asset class classification |
| **3** | `contract_id` | `str` | Non-empty string | Unique contract ID (e.g. `"NIFTY-SPOT"`, `"NIFTY26SEPFUT"`) |
| **4** | `exchange_timestamp` | `datetime` | UTC timezone-aware, bucket-aligned | Candle timestamp assigned by exchange |
| **5** | `received_timestamp` | `datetime` | UTC timezone-aware, $\ge exchange\_timestamp$ | Ingestion arrival timestamp |
| **6** | `timeframe` | `str` | Valid enum string (`"1m"`, `"3m"`, `"5m"`, `"15m"`) | Bar aggregation duration |
| **7** | `open` | `Decimal` | Strictly positive ($> 0$), finite, non-NaN | Opening traded price |
| **8** | `high` | `Decimal` | $\ge \max(open, close, low)$, finite, non-NaN | Highest traded price in interval |
| **9** | `low` | `Decimal` | $\le \min(open, close, high)$, finite, non-NaN | Lowest traded price in interval |
| **10** | `close` | `Decimal` | Strictly positive ($> 0$), finite, non-NaN | Closing traded price |
| **11** | `volume` | `int` | Non-negative ($\ge 0$) | Total traded volume in interval |
| **12** | `open_interest` | `int \| None` | Non-negative ($\ge 0$) or `None` | Open interest at interval close |
| **13** | `source` | `str` | Non-empty provenance string | Data venue/provider tag |
| **14** | `quality_status` | `DataQualityStatus` | Precedence-ranked enum member | Data quality classification |
| **15** | `data_version` | `int` | Default `1` | Schema revision number |
| **16** | `is_closed` | `bool` | Default `True` | `True` for closed bar; `False` for forming bar `[0]` |

---

## 2. Invariant Rules & Mathematical Boundaries

All instances of `MarketCandle` must satisfy:

1. **Strict Positivity of Prices:**
   $$\text{open} > 0 \quad\land\quad \text{high} > 0 \quad\land\quad \text{low} > 0 \quad\land\quad \text{close} > 0$$
2. **High Price Boundary:**
   $$\text{high} \ge \max(\text{open}, \text{close}, \text{low})$$
3. **Low Price Boundary:**
   $$\text{low} \le \min(\text{open}, \text{close}, \text{high})$$
4. **Volume Non-Negativity:**
   $$\text{volume} \ge 0$$
5. **Open Interest Non-Negativity:**
   $$\text{open\_interest} \text{ is None} \lor \text{open\_interest} \ge 0$$
6. **Temporal Causality:**
   $$\text{received\_timestamp} \ge \text{exchange\_timestamp}$$
7. **Timezone Awareness:**
   $$\text{exchange\_timestamp.tzinfo} = \text{UTC} \quad\land\quad \text{received\_timestamp.tzinfo} = \text{UTC}$$

---

## 3. Bridge Contract to Strategy Candle

The bridge method `MarketCandle.to_strategy_candle() -> Candle` maps canonical market data into the Phase 1 strategy execution format:

```python
Candle(
    timestamp=market_candle.exchange_timestamp,
    open=market_candle.open,
    high=market_candle.high,
    low=market_candle.low,
    close=market_candle.close,
    volume=market_candle.volume,
    open_interest=market_candle.open_interest,
    is_closed=market_candle.is_closed,
)
```

This guarantees 100% zero-conversion loss and perfect compatibility with the Phase 1 `DeterministicStrategyEngine`.

---

## 4. Execution Bridge Contract (`CandleStore.get_strategy_execution_input`)

The downstream strategy-data bridge enforces a strict, fail-closed contract:
1. **Real Forming Candle Required:** Slot `[0]` must contain an actual, observed forming candle (`is_closed=False`).
2. **Zero Synthetic Candles:** No synthetic, forward-filled, or zero-volume placeholder candles are ever constructed. If no real forming candle is present, `get_strategy_execution_input()` returns `None`.
3. **Execution Eligibility Guard:** If any candle in the execution series has an execution-blocking status (`INVALID`, `CONFLICT`, `GAP`, `STALE`, `INCOMPLETE`), `get_strategy_execution_input()` returns `None`.
4. **Sequence Guarantee:** When eligible, returns:
   - `[0]`: Real forming candle (`is_closed=False`, quarantined by strategy engine).
   - `[1]`: Latest fully closed candle (trigger candle).
   - `[2..N]`: Older fully closed candles in reverse chronological order.
