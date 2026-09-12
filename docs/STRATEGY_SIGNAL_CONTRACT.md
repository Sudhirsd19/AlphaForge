# ALPHAFORGE — STRATEGY SIGNAL CONTRACT & DECISION MODEL

**Project Name:** AlphaForge  
**Document Type:** Formal Data Contract & Decision Schema  
**Phase:** Phase 1 — Deterministic Strategy Specification  
**Author:** Principal Quantitative Systems Engineer  
**Date:** 2026-09-12  
**Status:** FROZEN  
**Master Source of Truth:** AlphaForge Development & Production-Ready Final Plan & Phase 0 Requirement Freeze

---

## 1. Immutable Signal Data Contract

Every decision produced by the Strategy Engine conforms to the immutable, strongly typed `StrategySignal` data model:

```python
from decimal import Decimal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class StrategyDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    INVALID_DATA = "INVALID_DATA"
    EXPIRED = "EXPIRED"
    DUPLICATE = "DUPLICATE"


class RejectionCode(str, Enum):
    REJECT_NONE = "REJECT_NONE"
    REJECT_CLOSED_CANDLE_VIOLATION = "REJECT_CLOSED_CANDLE_VIOLATION"
    REJECT_DATA_STALE = "REJECT_DATA_STALE"
    REJECT_DATA_INVALID = "REJECT_DATA_INVALID"
    REJECT_MISSING_TIMEFRAME = "REJECT_MISSING_TIMEFRAME"
    REJECT_INSUFFICIENT_HISTORY = "REJECT_INSUFFICIENT_HISTORY"
    REJECT_TREND = "REJECT_TREND"
    REJECT_BREAKOUT = "REJECT_BREAKOUT"
    REJECT_CANDLE_GEOMETRY = "REJECT_CANDLE_GEOMETRY"
    REJECT_VOLUME = "REJECT_VOLUME"
    REJECT_MOMENTUM = "REJECT_MOMENTUM"
    REJECT_VOLATILITY = "REJECT_VOLATILITY"
    REJECT_FUTURES_CONFIRMATION = "REJECT_FUTURES_CONFIRMATION"
    REJECT_INVALID_STOP = "REJECT_INVALID_STOP"
    REJECT_INVALID_TARGET = "REJECT_INVALID_TARGET"
    REJECT_EXPIRED = "REJECT_EXPIRED"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"


class FuturesConfirmationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    INVALID = "INVALID"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class TrendState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class StrategySignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    signal_id: str  # Deterministic SHA-256 hash string
    strategy_id: str  # e.g., "AF_ORB_MOMENTUM_V1"
    strategy_version: str  # e.g., "1.0.0"
    symbol: str  # e.g., "NIFTY"
    direction: SignalDirection  # LONG | SHORT | FLAT
    signal_timestamp: datetime  # Exact close timestamp of candle [1]
    evaluation_timestamp: datetime  # Context timestamp of engine evaluation
    entry_reference: Decimal  # Entry trigger price (Close of [1])
    stop_reference: Decimal  # Structural stop price
    target_reference: Decimal  # Fixed 1:2 profit target price
    risk_distance: Decimal  # abs(entry_reference - stop_reference)
    trend_state: TrendState  # BULLISH | BEARISH | NEUTRAL
    basis_status: FuturesConfirmationStatus  # CONFIRMED | NOT_CONFIRMED | etc.
    volume_status: str  # "CONFIRMED" | "BELOW_THRESHOLD"
    decision: StrategyDecision  # ACCEPT | REJECT | INVALID_DATA | EXPIRED | DUPLICATE
    rejection_code: RejectionCode  # Formal rejection code
    config_hash: str  # Hex digest of canonical config
    data_version: int  # Schema version
```

---

## 2. Decision Logic & Rejection Hierarchy

When `evaluate()` is called, the strategy proceeds through a strict linear gate hierarchy. Evaluation terminates at the first failing gate, ensuring unambiguous, single-cause rejection attribution:

```text
[ Incoming Market Data Context ]
                |
                v
       Gate 1: Closed-Candle Quarantine
       (Strip index [0]; verify >= 21 closed bars)  --> Fail: REJECT_CLOSED_CANDLE_VIOLATION / REJECT_INSUFFICIENT_HISTORY
                | Pass
                v
       Gate 2: Data Freshness Guard
       (Check delta between C1 and Teval)           --> Fail: REJECT_DATA_STALE
                | Pass
                v
       Gate 3: Multi-Timeframe Trend Filter
       (Check 15m EMA9 > EMA21 and Close > EMA21)   --> Fail: REJECT_TREND
                | Pass
                v
       Gate 4: 20-Period Breakout Check
       (Check Close(C1) > Resistance20)             --> Fail: REJECT_BREAKOUT
                | Pass
                v
       Gate 5: Candle Geometry Filter
       (Check BodyRatio >= 0.50 and Close in top 30%) --> Fail: REJECT_CANDLE_GEOMETRY
                | Pass
                v
       Gate 6: Relative Volume Spike Filter
       (Check Volume(C1) >= 1.20 * SMA20)           --> Fail: REJECT_VOLUME
                | Pass
                v
       Gate 7: Momentum Filter
       (Check 50.0 < RSI <= 75.0)                   --> Fail: REJECT_MOMENTUM
                | Pass
                v
       Gate 8: Volatility Bounds Filter
       (Check 0.05% * P <= ATR14 <= 1.50% * P)      --> Fail: REJECT_VOLATILITY
                | Pass
                v
       Gate 9: Futures Confirmation Interface
       (Check status == CONFIRMED)                  --> Fail: REJECT_FUTURES_CONFIRMATION
                | Pass
                v
       Gate 10: Stop-Loss & Target Boundaries
       (Verify RiskDistance within 0.10% - 3.0% P)   --> Fail: REJECT_INVALID_STOP / REJECT_INVALID_TARGET
                | Pass
                v
       Gate 11: Signal Expiry & Deduplication
       (Verify setup not expired or previously emitted) --> Fail: REJECT_EXPIRED / REJECT_DUPLICATE
                | Pass
                v
         [ EMIT DECISION: ACCEPT ]
```
