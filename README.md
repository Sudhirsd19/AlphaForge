# AlphaForge

AlphaForge is a deterministic, auditable, failure-tolerant, broker-reconcilable, and replayable quantitative futures trading system.

## Project Status

- **Current Phase:** Phase 18 — Institutional Hardening & Quant Validation (Certified)
- **Safety Setting:** `PAPER / REAL-MARKET SHADOW ONLY` (`LIVE_TRADING = FALSE`)
- **Test Suite Status:** 1,030 / 1,030 passing (100%) | 0 mypy issues | 0 ruff issues
- **Technical Quality Score:** 9.61 / 10.00
- **Master Specification:** AlphaForge Development & Production-Ready Final Plan

## Core Architecture

$$\text{Strategy} \neq \text{Risk} \neq \text{Execution} \neq \text{Broker Adapter} \neq \text{Reconciliation} \neq \text{Audit Ledger}$$

## Phase Sequence

```text
Phase 0  = Requirement Freeze
Phase 1  = Deterministic Strategy Specification
Phase 2  = Data Governance and Market Data Layer
Phase 3  = Futures and Contract Lifecycle Engine
Phase 4  = Index-Futures Basis Engine
Phase 5  = Risk Engine
Phase 6  = Cost and Slippage Model
Phase 7  = Order State Machine
Phase 8  = Idempotency and Crash Recovery
Phase 9  = Audit Ledger
Phase 10 = Backtest and Quant Validation
Phase 11 = Replay Engine
Phase 12 = Failure-Injection Testing
Phase 13 = Security and Compliance
Phase 14 = Observability
Phase 15 = Deployment Environments
Phase 16 = Paper / Shadow
Phase 17 = Real-Market Shadow / Certification
Phase 18 = Institutional Hardening & Quant Validation
```

## Documentation Reference

All formal system specifications and matrices are located in the `docs/` directory:
- [Phase 18 Final Acceptance Report](docs/PHASE18_FINAL_REPORT.md)
- [Requirement Freeze Specification](docs/REQUIREMENT_FREEZE.md)
- [Formal Requirement Traceability Matrix](docs/FORMAL_REQUIREMENT_MATRIX.md)
- [Open Design Decisions & Technical Proposals](docs/OPEN_DESIGN_DECISIONS.md)
- [Phase Acceptance Gates & Promotion Criteria](docs/ACCEPTANCE_GATES.md)
- [Master Development Roadmap](docs/DEVELOPMENT_ROADMAP.md)
- [Target Architecture Blueprint](docs/TARGET_ARCHITECTURE.md)
- [Current System Forensic Audit Report](docs/CURRENT_SYSTEM_AUDIT.md)
- [Technology Stack Specification](docs/TECHNOLOGY_STACK.md)
