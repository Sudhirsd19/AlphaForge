# ALPHAFORGE — OPEN DESIGN DECISIONS & TECHNICAL PROPOSALS

**Project Name:** AlphaForge  
**Document Type:** Architectural Trade-off Analysis & Open Technical Decisions  
**Phase:** Phase 0 — Requirement Freeze  
**Author:** Principal Software Architect, Senior Quantitative Systems Engineer, Reliability Engineer  
**Date:** 2026-09-12  
**Status:** PENDING USER APPROVAL / OPEN CONTRACT  
**Reference:** AlphaForge Master Constitution & Phase Boundary Correction

---

## 1. Architectural Intent vs Implementation Choice Policy

In accordance with Section 2 of the Phase Boundary Correction directive:
- **Mandatory Requirements:** Items explicitly mandated by the Master Specification (e.g. 17-state order machine, closed-candle rule `[1]`, independent risk engine, audit ledger, crash recovery).
- **Proposed Design Options:** Specific technologies, parameters, algorithms, and timing intervals that were proposed in the initial audit as candidate solutions but are **NOT** fixed by the Master Specification.
- **Strict Rule:** Proposed options must never silently become mandatory requirements. They remain open until formally reviewed, justified, and approved by the user.

---

## 2. Formal Catalog of Open Design Decisions

Below is the complete inventory of technical choices currently categorized as **PROPOSED**, **TBD**, or **REQUIRES APPROVAL**.

---

### DEC-01: Primary Programming Language & Runtime
- **Requirement Source:** Design proposal (Master Specification requires deterministic execution, type safety, and async broker reconciliation).
- **Status:** Proposed
- **Options Considered:**
  1. *Python 3.11+ / 3.12 (CPython):* Fast development, rich quant ecosystem (NumPy, Polars), mature broker APIs, strict `mypy --strict` typing. (Proposed default)
  2. *Rust:* Maximum zero-cost performance, memory safety, deterministic concurrency, but longer development cycle for rapid quant iteration.
  3. *Go:* Simple concurrency primitives, fast compilation, but weaker quantitative/scientific library ecosystem.
- **Reason for Proposal:** Python 3.11+ with strict typing and vectorized backends offers the optimal balance of speed to production, quantitative modeling capability, and maintainability for V1.
- **Approval Required:** Yes

---

### DEC-02: Live State Persistence & Audit Database Technology
- **Requirement Source:** Design proposal (Master Specification requires ACID state persistence, crash recovery, and tamper-evident audit ledger).
- **Status:** Proposed
- **Options Considered:**
  1. *SQLite in WAL Mode (via `aiosqlite`):* Single-file zero-network embedded database, sub-millisecond local fsync, crash-proof ACID transactions. (Proposed default)
  2. *PostgreSQL (TimescaleDB):* Robust client-server relational model, but introduces external network latency, socket dependency, and operational failure mode if database server restarts.
  3. *DuckDB:* Optimized for analytical queries, excellent for historical research, but less optimized for high-concurrency atomic row-level order state transitions.
- **Reason for Proposal:** SQLite with Write-Ahead Logging eliminates inter-process network hops, cannot be taken down by a network partition, and guarantees transactional durability for order states on local disk.
- **Approval Required:** Yes

---

### DEC-03: Historical Market Data & Backtest Storage Format
- **Requirement Source:** Design proposal (Master Specification requires historical data store for multi-timeframe backtesting and walk-forward validation).
- **Status:** Proposed
- **Options Considered:**
  1. *Apache Parquet + DuckDB:* Columnar, highly compressed, vectorized zero-copy analytical queries. (Proposed default)
  2. *Flat CSV:* Human-readable, but slow parsing, large disk footprint, and loss of explicit column typing.
  3. *PostgreSQL / TimescaleDB:* Centralized, but slower local scan speeds compared to columnar local Parquet.
- **Reason for Proposal:** Parquet files queried via DuckDB provide institutional-grade scanning speeds (millions of candles per second) for multi-year walk-forward and Monte Carlo runs.
- **Approval Required:** Yes

---

### DEC-04: Concrete Broker Adapter Implementation Target
- **Requirement Source:** Design proposal (Master Specification requires abstract `BrokerAdapterBase` with concrete broker support).
- **Status:** Proposed
- **Options Considered:**
  1. *Zerodha Kite Connect API:* Most widespread Indian retail/prop futures API, stable WebSocket and REST endpoints. (Proposed default for live)
  2. *Angel One SmartAPI:* Alternative Indian broker with WebSocket market stream.
  3. *Interactive Brokers (TWS / IB Gateway):* Global futures standard, complex local daemon requirement.
- **Reason for Proposal:** Zerodha Kite is the most battle-tested API for NIFTY/BANKNIFTY index futures in India.
- **Approval Required:** Yes

---

### DEC-05: Emergency Unprotected Position Watchdog Timing
- **Requirement Source:** Design proposal (Master Specification Section 14 mandates emergency handling for unprotected positions, but does not fix the exact timeout threshold).
- **Status:** Proposed (Requires Review)
- **Options Considered:**
  1. *5 Seconds:* High safety, allows 2-3 network round-trips for broker SL ACK before forcing emergency exit. (Proposed default)
  2. *3 Seconds:* Faster abort, but higher risk of false-positive emergency exits during temporary exchange network spikes.
  3. *10 Seconds:* More tolerant of broker latency, but leaves open capital exposed to market flash crashes for longer.
- **Reason for Proposal:** 5 seconds provides sufficient headroom for broker gateway acknowledgment while strictly bounding unprotected exposure time.
- **Approval Required:** Yes

---

### DEC-06: Cryptographic Hash Algorithm for Audit Ledger
- **Requirement Source:** Design proposal (Master Specification Section 17 mandates immutable tamper-evident event ledger with `previous_event_hash` and `event_hash`).
- **Status:** Proposed
- **Options Considered:**
  1. *SHA-256:* Industry standard NIST cryptographic hash function, hardware accelerated on modern CPUs. (Proposed default)
  2. *BLAKE3:* Significantly faster than SHA-256, but less universally standardized in regulatory audits.
  3. *HMAC-SHA256:* Adds a secret key to prevent ledger reconstruction even if attacker gains write access to the disk.
- **Reason for Proposal:** Standard SHA-256 meets all institutional compliance and audit requirements with negligible computation overhead ($< 5\mu s$ per event).
- **Approval Required:** Yes

---

### DEC-07: Index-Futures Basis Anomaly Z-Score Window & Threshold
- **Requirement Source:** Design proposal (Master Specification Section 11 requires basis z-score calculation and rejection of abnormal basis, but does not fix window length or sigma limit).
- **Status:** TBD (To be calibrated in Phase 4)
- **Options Considered:**
  1. *Rolling 20-Period Window with $\pm 2.5 \sigma$ limit:* Standard statistical mean-reversion band. (Proposed default)
  2. *Rolling 50-Period Window with $\pm 3.0 \sigma$ limit:* Smoother, filters out open-bell noise, but slower to adapt to intraday trend regime shifts.
  3. *Fixed Points Threshold:* Non-statistical absolute point spread (e.g. basis $> 50$ points).
- **Reason for Proposal:** Statistical z-score adapts dynamically to interest rate regimes and days-to-expiry decay, whereas fixed point spreads fail across differing contract tenors. Final parameters will be calibrated during Phase 4 backtesting.
- **Approval Required:** Yes

---

### DEC-08: Inter-Component Message Transport & Event Bus
- **Requirement Source:** Design proposal (Master Specification Section 6 mandates strict decoupling between Ingestion, Strategy, Risk, Execution, and Ledger).
- **Status:** Proposed
- **Options Considered:**
  1. *In-Process Asynchronous Typed Queues (`asyncio.Queue`):* Zero external infrastructure, sub-microsecond in-memory latency, zero network failure points. (Proposed default)
  2. *Redis / RabbitMQ:* External broker pub/sub, allows separate processes/containers, but adds network hop and external points of failure.
  3. *ZeroMQ / IPC:* High-speed inter-process memory pipes, but adds build complexity on Windows development environments.
- **Reason for Proposal:** In-process `asyncio.Queue` provides deterministic event ordering and zero external dependency risk for single-node deployment.
- **Approval Required:** Yes

---

### DEC-09: Real-Time Operational Alerting Channel
- **Requirement Source:** Design proposal (Master Specification Section 5 & 22 mandates operational alerts for P0/P1 events).
- **Status:** Proposed
- **Options Considered:**
  1. *Telegram Bot API:* Instant push notifications, mobile app support, zero cost, simple webhook. (Proposed default)
  2. *Slack Webhooks:* Institutional channel management, but requires paid plan for reliable high-frequency mobile push.
  3. *Local System Audio Siren + PagerDuty:* Maximum urgency for P0 emergencies (e.g., local sound alert if trading desk is manned).
- **Reason for Proposal:** Combination of Telegram Bot (mobile push) and local terminal sound alarm provides robust, multi-channel notification redundancy.
- **Approval Required:** Yes

---

### DEC-10: Secret Management & Key Injection Mechanism
- **Requirement Source:** Design proposal (Master Specification Section 21 mandates secrets outside source code and separation of paper/live credentials).
- **Status:** Proposed
- **Options Considered:**
  1. *Encrypted Environment File (`.env`) via Pydantic BaseSettings:* Lightweight, git-ignored, simple deployment. (Proposed default)
  2. *HashiCorp Vault:* Enterprise-grade secret leasing and rotation, but requires dedicated infrastructure server.
  3. *OS Keyring (Windows Credential Manager / Linux Secret Service):* Secure OS-native storage, but requires platform-specific automation scripts.
- **Reason for Proposal:** Pydantic Settings with strict validation and OS environment variables ensures zero credentials in version control while maintaining simplicity.
- **Approval Required:** Yes

---

### DEC-11: V1 Deterministic Strategy Baseline Parameters
- **Requirement Source:** User Directive (Authoritative Strategy Parameter Sign-Off, 2026-09-12).
- **Status:** **APPROVED BY USER (2026-09-12)**
- **Scope & Baseline Values:**
  1. *Trend Filter (Higher Timeframe):*
     - Confirmation timeframe: `15m`
     - Fast EMA: `9`
     - Slow EMA: `21`
     - Bullish condition: $EMA_9 > EMA_{21} \land Close > EMA_{21}$
     - Bearish condition: $EMA_9 < EMA_{21} \land Close < EMA_{21}$
  2. *Breakout (Execution Timeframe):*
     - Execution timeframe: `3m`
     - Lookback: `20` closed bars
     - Long breakout: $Close(C_1) > \max(High[2..21])$
     - Short breakout: $Close(C_1) < \min(Low[2..21])$
  3. *Candle Geometry (Execution Timeframe):*
     - Minimum body ratio: $\frac{\text{Body}}{\text{Range}} \ge 0.50$
     - Minimum close-location ratio: $\ge 0.70$ (Top 30% for Long, Bottom 30% for Short)
     - Closed candles only (forming candle quarantined).
  4. *Volume Confirmation (Execution Timeframe):*
     - Lookback: `20` closed bars
     - Condition: $Volume(C_1) \ge 1.20 \times \text{SMA}(Volume, 20)_{[2..21]}$
  5. *Momentum Filter (Execution Timeframe):*
     - RSI period: `14`
     - Long momentum: $50.0 < RSI \le 75.0$
     - Short momentum: $25.0 \le RSI < 50.0$
  6. *Volatility Filter (Execution Timeframe):*
     - ATR period: `14`
     - Volatility range: $0.05\% \le \frac{ATR_{14}}{Price} \le 1.50\%$
  7. *Stop-Loss Reference (Execution Timeframe):*
     - Structural swing lookback: `swing_stop_lookback = 2` bars
     - ATR multiplier: `1.0`
     - Long Stop: $P_{stop} = \min(Low[1], Low[2]) - 1.0 \times ATR_{14}$
     - Short Stop: $P_{stop} = \max(High[1], High[2]) + 1.0 \times ATR_{14}$
  8. *Target Reference (Execution Timeframe):*
     - Fixed Risk:Reward = `1:2`
     - Long Target: $P_{target} = P_{entry} + 2.0 \times \text{RiskDistance}$
     - Short Target: $P_{target} = P_{entry} - 2.0 \times \text{RiskDistance}$
  9. *Risk Distance Guardrails:*
     - Bounds: $0.10\% \le \frac{\text{RiskDistance}}{P_{entry}} \le 3.00\%$
  10. *Data Freshness Guard:*
      - Max stale duration: `195 seconds` (180s 3m execution candle + 15s grace).
- **Important Quantitative Disclaimer:**
  These parameters are approved as the **V1 strategy baseline only**. They are NOT claimed to be profitable, optimal, statistically superior, production validated, backtest validated, or market-regime robust. Quantitative validation belongs strictly to Phase 10. No AI/ML, sentiment, or parameter curve-fitting is permitted.
- **Approval Required:** Formally approved by User on 2026-09-12.

---

## 3. Summary of Open Decisions & Next Steps

| Decision ID | Area | Default Proposal | Status | Impact on Phase 1 |
| :--- | :--- | :--- | :--- | :--- |
| **DEC-01** | Language & Runtime | Python 3.11+ (strict typing) | Approved | Active runtime for Phase 1. |
| **DEC-02** | Live DB & Ledger | SQLite (WAL mode) | Proposed | Deferred to Phase 7-9 (does not block Phase 1). |
| **DEC-03** | Research Store | DuckDB + Parquet | Proposed | Deferred to Phase 2/10 (does not block Phase 1). |
| **DEC-04** | Broker Adapter | Zerodha Kite + Simulated Paper | Proposed | Deferred to Phase 8/16 (does not block Phase 1). |
| **DEC-05** | SL Watchdog Delay | 5.0 Seconds | Proposed | Deferred to Phase 7 (does not block Phase 1). |
| **DEC-06** | Ledger Hash Algorithm | SHA-256 | Proposed | Deferred to Phase 9 (does not block Phase 1). |
| **DEC-07** | Basis Z-Score Window | 20-Period / $\pm 2.5\sigma$ | TBD | Deferred to Phase 4 calibration. |
| **DEC-08** | Message Transport | In-Process `asyncio.Queue` | Proposed | Baseline architectural pattern. |
| **DEC-09** | Alerting Channel | Telegram Bot + Terminal Sound | Proposed | Deferred to Phase 14. |
| **DEC-10** | Secrets Manager | Pydantic Settings + Env | Proposed | Deferred to Phase 13. |
| **DEC-11** | V1 Strategy Parameters | 21 Baseline Rules & Values | **Approved** | Formally signed-off; active baseline. |

