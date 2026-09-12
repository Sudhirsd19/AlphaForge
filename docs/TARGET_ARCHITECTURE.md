# ALPHAFORGE — TARGET ARCHITECTURE SPECIFICATION

**Project Name:** AlphaForge  
**Author:** Principal Software Architect, Senior Quantitative Trading Systems Engineer  
**Date:** 2026-09-12  
**Status:** Approved Target Architectural Blueprint  
**Baseline Reference:** AlphaForge Master Constitution (Sections 6, 7, 30)

---

## 1. Architectural Evolution: Current vs Target vs Gap

```text
CURRENT STATE:
  Workspace: d:\AlphaForge
  Maturity: Level 0 (Clean-Slate Greenfield)
  Source Modules: None
  Running Services: None
  GAP: 100% of Master Specification architecture to be constructed.

TARGET STATE:
  A deterministic, auditable, failure-tolerant, broker-reconcilable, and replayable
  institutional trading system enforcing strict layer isolation and fail-closed safety.
```

---

## 2. End-to-End Target Architecture Diagram

```text
                                 [ Market Data Sources ]
                                (WebSocket & REST Streams)
                                             |
                                             v
                                 [ Data Ingestion Layer ]
                               (Feed Multiplexing & Parsing)
                                             |
                                             v
                            [ Data Quality & Normalization ]
                         (Strict OHLC, Monotonicity & Gaps)
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
                   v                                                   v
            [ Candle Store ]                                  [ Contract Master ]
     (DuckDB / Parquet / In-Memory)                   (Lot Sizes, Expiry & Rollover)
                   |                                                   |
                   +-------------------------+-------------------------+
                                             |
                                             v
                               [ Indicator & Feature Engine ]
                                (Pure Vectorized Functions)
                               (Closed Candle [1] Quarantine)
                                             |
                                             v
                                 [ Strategy Signal Engine ]
                               (Multi-Timeframe Deterministic)
                                             |
                                             v
                                    [ Signal Validator ]
                               (Stale Data, Divergence Gates)
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
                   v                                                   v
            [ Basis Engine ]                                    [ Risk Engine ]
      (Index-Futures Spread & Z-Score)                    (Daily Loss, Open Risk, Lots)
                   |                                                   |
                   +-------------------------+-------------------------+
                                             |
                                             v
                              [ Execution Decision Engine ]
                             (Order Intent & Sizing Synthesis)
                                             |
                                             v
                                 [ Order State Machine ]
                             (17 Deterministic Order States)
                                             |
                                             v
                                     [ Broker Adapter ]
                             (Rate Limiter, Gateway Translator)
                                             |
                                             v
                                 [ Reconciliation Engine ]
                             (Broker vs Local State Sync Loop)
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
                   v                                                   v
            [ Audit Ledger ]                                [ Monitoring & Alerts ]
     (Cryptographic Hash-Chained WAL)                 (Health, Heartbeats, Kill Switch)
```

---

## 3. Strict Boundary & Responsibility Decoupling (Section 7)

```text
+---------------------------------------------------------------------------------------+
|                               RESPONSIBILITY BOUNDARIES                               |
+---------------------+-----------------------------------------------------------------+
| Strategy Engine     | • MAY compute technical features and evaluate entry/exit rules. |
|                     | • MAY emit SignalIntent with price levels and timestamps.        |
|                     | • MUST NOT place broker orders, calculate lot sizing, or retry. |
+---------------------+-----------------------------------------------------------------+
| Risk Engine         | • Decoupled gatekeeper with absolute veto authority.            |
|                     | • Enforces Max Daily Loss, Open Risk ceiling, and margin.       |
|                     | • Computes exact discrete lot quantity: floor(risk / sl_dist).   |
+---------------------+-----------------------------------------------------------------+
| Execution Engine    | • Translates approved RiskIntent into idempotent client orders.  |
|                     | • Manages the 17-state Order State Machine.                     |
|                     | • Monitors Stop-Loss placement; triggers Emergency Protocol if   |
|                     |   unprotected fill exceeds 5 seconds.                           |
+---------------------+-----------------------------------------------------------------+
| Broker Adapter      | • Pure transport and protocol translation layer.                 |
|                     | • Handles authentication, connection pooling, and rate limits.  |
|                     | • Normalizes external broker payloads to internal events.       |
+---------------------+-----------------------------------------------------------------+
| Reconciliation      | • Periodic and cold-boot startup state synchronizer.            |
| Engine              | • Compares local open orders/positions against broker API truth. |
|                     | • Resolves UNKNOWN orders; blocks new trades upon any mismatch. |
+---------------------+-----------------------------------------------------------------+
| Audit Ledger        | • Tamper-evident append-only SQLite WAL store.                  |
|                     | • SHA-256 hash chaining of all state mutations and decisions.   |
+---------------------+-----------------------------------------------------------------+
```

---

## 4. Greenfield Directory & Module Layout

The following modular directory structure will be realized during implementation:

```text
d:\AlphaForge\
├── .gitignore
├── pyproject.toml
├── README.md
├── config/
│   ├── settings.py              # Pydantic BaseSettings (env, credentials, paths)
│   ├── instruments.yaml         # Instrument definitions (lot sizes, tick sizes)
│   └── risk_limits.yaml         # Daily loss limits, max open risk thresholds
├── alphaforge/
│   ├── __init__.py
│   ├── core/                    # Immutable domain models and base types
│   │   ├── models.py            # MarketCandle, Signal, Order, Position, Event
│   │   ├── enums.py             # OrderState, SignalDirection, RejectionCode
│   │   └── exceptions.py        # AlphaForge base exceptions
│   ├── data/                    # Market data pipeline
│   │   ├── ingestion.py         # WebSocket and REST stream handlers
│   │   ├── validator.py         # Invariant assertions (monotonicity, OHLC, gaps)
│   │   ├── resampler.py         # Multi-timeframe bar builder
│   │   └── store.py             # DuckDB / Parquet storage interface
│   ├── indicators/              # Vectorized feature engine
│   │   ├── moving_averages.py   # EMA, SMA, WMA
│   │   ├── volatility.py        # ATR, SuperTrend, Bollinger Bands
│   │   └── basis.py             # Index vs Futures basis points & z-score
│   ├── strategy/                # Deterministic signal generation
│   │   ├── engine.py            # Multi-timeframe strategy evaluation
│   │   ├── rules.py             # Pure functional entry/exit conditions
│   │   └── validator.py         # Signal gatekeeper (rejection emission)
│   ├── risk/                    # Independent risk gateway
│   │   ├── engine.py            # Risk approval orchestrator
│   │   ├── sizer.py             # Discrete lot-size math & rounding
│   │   └── limits.py            # Daily loss & open exposure circuit breakers
│   ├── execution/               # Order lifecycle & state machine
│   │   ├── state_machine.py     # 17-state FSM implementation
│   │   ├── idempotency.py       # Deterministic client_order_id generator
│   │   └── orchestrator.py      # Execution coordinator & emergency watchdog
│   ├── broker/                  # Broker abstraction
│   │   ├── base.py              # BrokerAdapterBase abstract interface
│   │   ├── paper.py             # High-fidelity simulated paper broker
│   │   └── kite.py              # Zerodha Kite Connect live/shadow adapter
│   ├── reconciliation/          # State synchronization & recovery
│   │   ├── reconciler.py        # Broker vs local state comparator
│   │   └── recovery.py          # 9-step startup cold boot recovery
│   ├── audit/                   # Audit ledger & logging
│   │   ├── ledger.py            # Cryptographically hash-chained SQLite WAL store
│   │   └── logger.py            # Structured JSON structlog configuration
│   └── monitoring/              # Telemetry & alerts
│       ├── health.py            # Component heartbeat and latency monitor
│       ├── notifier.py          # Telegram / Webhook emergency dispatcher
│       └── kill_switch.py       # Global emergency halt coordinator
├── docs/                        # Complete architecture & audit documentation
└── tests/                       # Comprehensive test pyramid
    ├── unit/                    # Fast isolated component unit tests
    ├── property/                # Hypothesis property-based invariant tests
    ├── integration/             # Multi-module state machine & recovery tests
    ├── golden/                  # Fixture-backed deterministic replay tests
    └── failure_injection/       # 17 chaos failure mode simulation tests
```
