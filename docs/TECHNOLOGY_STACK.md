# ALPHAFORGE — TECHNOLOGY STACK SPECIFICATION

**Project Name:** AlphaForge  
**Author:** Principal Software Architect, Senior Quantitative Trading Systems Engineer  
**Date:** 2026-09-12  
**Status:** Approved Target Specification  

---

## 1. Overview and Selection Criteria

The technology stack for AlphaForge is selected to guarantee **determinism**, **type safety**, **sub-millisecond state transitions**, **tamper-evident auditability**, and **strict crash resilience**. Because trading futures involves substantial financial risk, non-deterministic runtimes, loose type systems, and unversioned dependencies are strictly prohibited.

---

## 2. Core Language & Runtime Environment

- **Language:** Python 3.11+ or Python 3.12 (Standard CPython)
  - *Rationale:* Native asyncio enhancements, exception groups for multi-task broker reconciliation, and mature quant ecosystem.
- **Type Checking:** `mypy` running with `--strict` mode.
  - *Requirement:* 100% type annotation coverage across all strategy, risk, data, and execution code. No `Any` types allowed in core domain models.
- **Code Hygiene & Linting:** `ruff` (combining Flake8, Black, isort, Bandit, and pyupgrade).
  - *Requirement:* Zero lint warnings, zero format drifts, zero security flags.

---

## 3. Domain Modeling & Data Contracts

- **Validation & Serialization:** `Pydantic v2` (`pydantic.BaseModel` and `dataclasses`).
  - *Configuration:* `frozen=True` for all domain events, market data ticks, closed candles, signals, and risk approval objects to guarantee immutability.
  - *Strict Parsing:* `extra="forbid"`, `strict=True` to prevent accidental property injection or silent data coercions.

---

## 4. Quantitative & Indicator Engine

- **Numerical Computing:** `NumPy` (vectorized matrix computations).
- **DataFrames & Timeseries:** `pandas` and `polars` (arrow-backed high-speed timeseries manipulations).
- **Technical Analysis:** Vectorized custom implementations of EMAs, ATR, SuperTrend, Bollinger Bands, and custom Basis indicators.
  - *Requirement:* All indicator routines must be pure functions taking explicit arrays and returning deterministic output without internal stateful side-effects.

---

## 5. Persistence & Storage Layer

AlphaForge employs a **bifurcated storage architecture** tailored to specific latency and throughput requirements:

### 5.1 Historical Research & Backtest Store
- **Engine:** `DuckDB` + Apache `Parquet`.
- **Purpose:** Compressed, columnar storage of multi-year minute candles, tick archives, and walk-forward validation runs.
- **Characteristics:** Zero-copy scans, fast vectorized aggregations, reproducible offline queries.

### 5.2 Live Trading State, Order State Machine & Audit Ledger
- **Engine:** `SQLite` with **Write-Ahead Logging (WAL)** enabled (`aiosqlite` for asynchronous operations).
- **Configuration:**
  - `PRAGMA journal_mode = WAL;`
  - `PRAGMA synchronous = NORMAL;`
  - `PRAGMA busy_timeout = 5000;`
  - `PRAGMA foreign_keys = ON;`
- **Purpose:** Atomic transaction guarantees for order state transitions, idempotency keys, position tracking, and hash-chained audit records.
- **Characteristics:** Embedded, zero-network dependency, crash-proof ACID compliance.

---

## 6. Asynchronous Networking & Broker Interfaces

- **Async Runtime:** Python standard `asyncio`.
- **HTTP Client:** `httpx` (async client with explicit connection pooling and strict timeout configurations).
- **WebSocket Client:** `websockets` / `aiohttp` with built-in exponential backoff reconnection, heartbeat monitors, and sequence-gap detectors.
- **Broker Target Adapters:**
  - Abstract base adapter `BrokerAdapterBase`.
  - Concrete adapters: Zerodha Kite Connect API / Angel One SmartAPI / Paper Broker Simulator.

---

## 7. Testing & Quality Assurance Suite

- **Unit & Integration Testing:** `pytest` and `pytest-asyncio`.
- **Property-Based Testing:** `hypothesis` for invariant fuzzing (testing closed-candle rules, lot-size rounding math, and order state machine transition matrices).
- **Test Coverage:** `pytest-cov` with a strict minimum target of **90%+ test coverage** on core execution, risk, and data packages.
- **Replay Testing:** Bespoke deterministic event replay runner using recorded market data fixtures.

---

## 8. Logging, Audit & Telemetry

- **Structured Logging:** `structlog` outputting machine-readable JSON format to stdout and local rolling file.
- **Tamper-Evident Ledger:** SHA-256 cryptographic hash-chaining where each audit event includes `previous_event_hash` and generates `event_hash = sha256(prev_hash + event_data)`.
- **Alerting & Notifications:** Telegram Bot API / Webhooks for P0 emergency escalation (unprotected positions, reconciliation mismatches, kill-switch triggers).

---

## 9. Dependency Manifest & Isolation

All dependencies will be locked via `pyproject.toml` and deterministic lockfiles (`uv.lock` or `requirements.txt` with SHA-256 hashes). No untracked or floating dependency versions are permitted in production or testing environments.
