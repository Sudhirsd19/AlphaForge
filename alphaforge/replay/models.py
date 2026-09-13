"""
AlphaForge Replay Engine Domain Models.
Defines immutable data models, manifests, state snapshots, checkpoints,
divergence diagnostics, and replay results.
Guarantees deterministic identity, canonical serialization, and strict schema validation.
"""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.backtest.models import BacktestTrade, TraceEntry
from alphaforge.core.exceptions import ReplayIntegrityError
from alphaforge.execution.enums import OrderState
from alphaforge.ledger.serialization import canonical_json
from alphaforge.risk.enums import TradeSide

REPLAY_SCHEMA_VERSION = "1.0.0"
REPLAY_ENGINE_VERSION = "1.0.0"


class ReplayStatus(StrEnum):
    """Authoritative status of replay execution."""

    PASS = "PASS"  # noqa: S105
    WARNING = "WARNING"
    FAIL = "FAIL"


class ReplayMode(StrEnum):
    """Operational mode for the Replay Engine."""

    FULL = "FULL"
    VALIDATION_ONLY = "VALIDATION_ONLY"
    PREFIX = "PREFIX"


class ReplayManifest(BaseModel):
    """
    Immutable provenance and identity manifest for a replay execution.
    Contains all deterministic attributes of the replayed source artifact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_run_id: str = Field(description="Unique run ID of the source artifact")
    source_dataset_id: str = Field(description="Source dataset identifier")
    source_dataset_checksum: str = Field(description="Source dataset checksum")
    source_strategy_id: str = Field(description="Source strategy identifier")
    source_strategy_version: str = Field(description="Source strategy revision")
    source_engine_version: str = Field(description="Source backtest/execution engine version")
    source_result_canonical_hash: str | None = Field(
        default=None, description="Authoritative canonical result hash of source, if available"
    )
    source_trace_canonical_hash: str | None = Field(
        default=None, description="Authoritative canonical trace hash of source, if available"
    )
    source_contract_id: str | None = Field(
        default=None, description="Source instrument/contract identifier"
    )
    source_risk_config_fingerprint: str | None = Field(
        default=None, description="SHA-256 fingerprint of source risk configuration"
    )
    source_cost_config_fingerprint: str | None = Field(
        default=None, description="SHA-256 fingerprint of source cost configuration"
    )
    source_replay_schema_version: str = Field(
        default=REPLAY_SCHEMA_VERSION, description="Schema revision of replay manifest"
    )
    replay_engine_version: str = Field(
        default=REPLAY_ENGINE_VERSION, description="Version of the Replay Engine"
    )

    @field_validator("source_run_id", "source_dataset_id", "source_strategy_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ReplayIntegrityError("Manifest field cannot be empty")
        return clean

    def compute_replay_id(self) -> str:
        """
        Derive deterministic replay run ID from canonical manifest contents.
        Format: REPLAY-[24 uppercase hex chars].
        Strictly independent of current system time or environment.
        """
        manifest_dict = {
            "replay_engine_version": self.replay_engine_version,
            "source_contract_id": self.source_contract_id,
            "source_cost_config_fingerprint": self.source_cost_config_fingerprint,
            "source_dataset_checksum": self.source_dataset_checksum,
            "source_dataset_id": self.source_dataset_id,
            "source_engine_version": self.source_engine_version,
            "source_replay_schema_version": self.source_replay_schema_version,
            "source_result_canonical_hash": self.source_result_canonical_hash,
            "source_risk_config_fingerprint": self.source_risk_config_fingerprint,
            "source_run_id": self.source_run_id,
            "source_strategy_id": self.source_strategy_id,
            "source_strategy_version": self.source_strategy_version,
            "source_trace_canonical_hash": self.source_trace_canonical_hash,
        }
        canonical_repr = canonical_json(manifest_dict)
        h = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest().upper()
        return f"REPLAY-{h[:24]}"


class ReplayConfig(BaseModel):
    """
    Configuration parameters for Replay Engine execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    mode: ReplayMode = Field(default=ReplayMode.FULL, description="Replay mode")
    checkpoint_interval: int = Field(
        default=10, ge=1, description="Event interval between state checkpoints"
    )
    prefix_cutoff_sequence: int | None = Field(
        default=None, ge=1, description="Optional sequence cutoff for prefix replay"
    )
    prefix_cutoff_timestamp: datetime | None = Field(
        default=None, description="Optional UTC timestamp cutoff for prefix replay"
    )
    prefix_cutoff_event_id: str | None = Field(
        default=None, description="Optional event ID cutoff for prefix replay"
    )
    verify_audit_hash_chain: bool = Field(
        default=True, description="Verify cryptographic hash chain integrity"
    )
    verify_fsm_transitions: bool = Field(
        default=True, description="Enforce formal Phase 7 FSM legality"
    )
    verify_execution_trace: bool = Field(
        default=True, description="Verify reconstructed trace against source trace hash"
    )
    verify_result_hash: bool = Field(
        default=True, description="Verify reconstructed result against source result hash"
    )
    fail_on_first_divergence: bool = Field(
        default=True, description="Fail immediately upon encountering the first divergence"
    )

    @field_validator("prefix_cutoff_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v)):
            raise ReplayIntegrityError(f"Cutoff timestamp must be timezone-aware UTC: {v}")
        return v


class ReplayOrderState(BaseModel):
    """Reconstructed state of an order during replay."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: str = Field(description="Unique order identifier")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="LONG or SHORT")
    quantity: int = Field(gt=0, description="Order quantity")
    state: OrderState = Field(description="Current formal FSM state")
    filled_quantity: int = Field(default=0, ge=0, description="Total filled quantity")
    average_price: Decimal | None = Field(default=None, description="Average execution price")
    signal_id: str | None = Field(default=None, description="Associated strategy signal ID")
    is_protection_confirmed: bool = Field(
        default=False, description="Whether protection order is confirmed"
    )
    created_at: datetime = Field(description="UTC timestamp of order creation")
    updated_at: datetime = Field(description="UTC timestamp of most recent transition")
    history_event_types: tuple[str, ...] = Field(
        default_factory=tuple, description="Ordered history of event types applied"
    )


class ReplayPositionState(BaseModel):
    """Reconstructed state of market position during replay."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    has_open_position: bool = Field(default=False, description="True if position is currently open")
    symbol: str | None = Field(default=None, description="Open position symbol")
    side: TradeSide | None = Field(default=None, description="LONG or SHORT")
    quantity: int = Field(default=0, ge=0, description="Open position quantity")
    entry_reference_price: Decimal = Field(
        default=Decimal("0"), description="Reference price at entry"
    )
    entry_effective_price: Decimal = Field(
        default=Decimal("0"), description="Effective execution price after slippage"
    )
    entry_timestamp: datetime | None = Field(
        default=None, description="UTC timestamp of position opening"
    )
    entry_signal_id: str | None = Field(default=None, description="Associated entry signal ID")
    strategy_version: str | None = Field(default=None, description="Strategy version at entry")
    entry_fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), description="Entry fee")
    entry_slippage: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Entry slippage cost"
    )
    notional_exposure: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Current notional exposure"
    )
    margin_used: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Current margin requirement"
    )


class ReplayPortfolioState(BaseModel):
    """Reconstructed portfolio and collateral accounting state during replay."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    initial_capital: Decimal = Field(
        default=Decimal("1000000"), gt=Decimal("0"), description="Starting capital"
    )
    cash: Decimal = Field(default=Decimal("1000000"), description="Available cash")
    margin_used: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Margin currently committed"
    )
    cumulative_realized_pnl: Decimal = Field(
        default=Decimal("0"), description="Cumulative realized gross PnL"
    )
    unrealized_pnl: Decimal = Field(
        default=Decimal("0"), description="Mark-to-market unrealized PnL"
    )
    cumulative_fees: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Total transaction fees"
    )
    cumulative_slippage: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Total execution slippage"
    )
    cumulative_other_costs: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), description="Other friction costs"
    )
    equity: Decimal = Field(default=Decimal("1000000"), description="Total portfolio equity")
    completed_trades_count: int = Field(default=0, ge=0, description="Completed round-trip trades")


class ReplayContractState(BaseModel):
    """Reconstructed contract lifecycle and expiry state during replay."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    contract_id: str | None = Field(default=None, description="Contract ID if derivative")
    expiry_datetime: datetime | None = Field(
        default=None, description="Authoritative expiry UTC datetime"
    )
    lot_size: int = Field(default=1, ge=1, description="Standard contract lot size")
    contract_multiplier: Decimal = Field(
        default=Decimal("1"), gt=Decimal("0"), description="Contract multiplier"
    )
    expired_trade_attempts: int = Field(
        default=0, ge=0, description="Rejected entry attempts at/after expiry"
    )
    post_expiry_fills: int = Field(
        default=0, ge=0, description="Unauthorized fills executed after expiry (violation)"
    )
    is_expired: bool = Field(default=False, description="True if contract reached expiry")
    lifecycle_violation: bool = Field(
        default=False, description="True if any lifecycle violation occurred"
    )


class ReplayRiskState(BaseModel):
    """Reconstructed risk evaluation telemetry during replay."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    risk_evaluations: int = Field(default=0, ge=0, description="Total risk checks performed")
    risk_approvals: int = Field(default=0, ge=0, description="Total risk proposals approved")
    risk_rejections: int = Field(default=0, ge=0, description="Total risk proposals rejected")
    circuit_breaker_active: bool = Field(
        default=False, description="True if circuit breaker triggered"
    )
    risk_config_fingerprint: str | None = Field(
        default=None, description="Active RiskConfig canonical fingerprint"
    )


class ReplayState(BaseModel):
    """
    Authoritative composite domain state reconstructed at a point in replay.
    Fully canonicalizable and hashable into a state fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    portfolio: ReplayPortfolioState = Field(default_factory=ReplayPortfolioState)
    position: ReplayPositionState = Field(default_factory=ReplayPositionState)
    orders: dict[str, ReplayOrderState] = Field(default_factory=dict)
    contract: ReplayContractState = Field(default_factory=ReplayContractState)
    risk: ReplayRiskState = Field(default_factory=ReplayRiskState)
    trades: tuple[BacktestTrade, ...] = Field(default_factory=tuple)
    trace: tuple[TraceEntry, ...] = Field(default_factory=tuple)
    last_processed_sequence: int = Field(default=0, ge=0)
    last_event_id: str | None = Field(default=None)
    last_event_hash: str | None = Field(default=None)
    last_event_timestamp: datetime | None = Field(default=None)

    def compute_fingerprint(self) -> str:
        """
        Compute deterministic SHA-256 fingerprint of canonicalized state.
        Guarantees identical fingerprint for identical state.
        """
        state_payload = {
            "contract": {
                "contract_id": self.contract.contract_id,
                "contract_multiplier": str(self.contract.contract_multiplier),
                "expired_trade_attempts": self.contract.expired_trade_attempts,
                "expiry_datetime": (
                    self.contract.expiry_datetime.astimezone(UTC).isoformat()
                    if self.contract.expiry_datetime
                    else None
                ),
                "is_expired": self.contract.is_expired,
                "lifecycle_violation": self.contract.lifecycle_violation,
                "lot_size": self.contract.lot_size,
                "post_expiry_fills": self.contract.post_expiry_fills,
            },
            "last_event_hash": self.last_event_hash,
            "last_event_id": self.last_event_id,
            "last_processed_sequence": self.last_processed_sequence,
            "orders": {
                order_id: {
                    "average_price": str(o.average_price) if o.average_price is not None else None,
                    "filled_quantity": o.filled_quantity,
                    "history_event_types": list(o.history_event_types),
                    "is_protection_confirmed": o.is_protection_confirmed,
                    "order_id": o.order_id,
                    "quantity": o.quantity,
                    "side": o.side.value,
                    "signal_id": o.signal_id,
                    "state": o.state.value,
                    "symbol": o.symbol,
                }
                for order_id, o in sorted(self.orders.items())
            },
            "portfolio": {
                "cash": str(self.portfolio.cash),
                "completed_trades_count": self.portfolio.completed_trades_count,
                "cumulative_fees": str(self.portfolio.cumulative_fees),
                "cumulative_other_costs": str(self.portfolio.cumulative_other_costs),
                "cumulative_realized_pnl": str(self.portfolio.cumulative_realized_pnl),
                "cumulative_slippage": str(self.portfolio.cumulative_slippage),
                "equity": str(self.portfolio.equity),
                "initial_capital": str(self.portfolio.initial_capital),
                "margin_used": str(self.portfolio.margin_used),
                "unrealized_pnl": str(self.portfolio.unrealized_pnl),
            },
            "position": {
                "entry_effective_price": str(self.position.entry_effective_price),
                "entry_fee": str(self.position.entry_fee),
                "entry_reference_price": str(self.position.entry_reference_price),
                "entry_signal_id": self.position.entry_signal_id,
                "entry_slippage": str(self.position.entry_slippage),
                "has_open_position": self.position.has_open_position,
                "margin_used": str(self.position.margin_used),
                "notional_exposure": str(self.position.notional_exposure),
                "quantity": self.position.quantity,
                "side": self.position.side.value if self.position.side is not None else None,
                "strategy_version": self.position.strategy_version,
                "symbol": self.position.symbol,
            },
            "risk": {
                "circuit_breaker_active": self.risk.circuit_breaker_active,
                "risk_approvals": self.risk.risk_approvals,
                "risk_config_fingerprint": self.risk.risk_config_fingerprint,
                "risk_evaluations": self.risk.risk_evaluations,
                "risk_rejections": self.risk.risk_rejections,
            },
            "trades_count": len(self.trades),
            "trace_events_count": len(self.trace),
        }
        canonical_repr = canonical_json(state_payload)
        return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


class ReplayCheckpoint(BaseModel):
    """
    Immutable state checkpoint recorded at a deterministic sequence boundary.
    Supports crash recovery and exact resumption.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sequence_number: int = Field(ge=1, description="Ledger sequence number at checkpoint")
    event_id: str = Field(description="Event ID at checkpoint boundary")
    timestamp: datetime = Field(description="UTC timestamp of the boundary event")
    state_fingerprint: str = Field(description="Canonical SHA-256 fingerprint of replayed state")
    state_snapshot: ReplayState = Field(description="Full captured domain state at boundary")


class ReplayMismatch(BaseModel):
    """
    Authoritative forensic diagnostics for the exact first divergence detected during replay.
    Identifies the divergent event, sequences, state before/after, category, and diagnostic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    divergent_sequence: int = Field(ge=0, description="Sequence number where divergence occurred")
    event_id: str = Field(description="Identifier of divergent event, or NONE")
    event_type: str = Field(description="Type of event where divergence was flagged")
    source_fingerprint: str = Field(description="Expected source hash or fingerprint")
    replay_fingerprint: str = Field(description="Actual replayed hash or fingerprint")
    source_state_before: str | None = Field(
        default=None, description="Source state fingerprint before transition"
    )
    replay_state_before: str | None = Field(
        default=None, description="Replay state fingerprint before transition"
    )
    source_state_after: str | None = Field(
        default=None, description="Source state fingerprint after transition"
    )
    replay_state_after: str | None = Field(
        default=None, description="Replay state fingerprint after transition"
    )
    mismatch_category: str = Field(
        description="Categorized failure code, e.g. HASH_CHAIN_DIVERGENCE, FSM_ILLEGAL_TRANSITION"
    )
    diagnostic: str = Field(description="Human-readable actionable forensic explanation")


class ReplayResult(BaseModel):
    """
    Deterministic structured output of a Replay Engine execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: ReplayStatus = Field(description="PASS, WARNING, or FAIL")
    replay_id: str = Field(description="Deterministic REPLAY-[24_HEX] identifier")
    source_run_id: str = Field(description="Run ID of the replayed source artifact")
    source_manifest: ReplayManifest = Field(description="Authoritative source artifact manifest")
    mode: ReplayMode = Field(description="Executed replay mode")
    events_processed: int = Field(ge=0, description="Total events processed by replay engine")
    events_verified: int = Field(ge=0, description="Total events that passed all integrity checks")
    final_state: ReplayState | None = Field(
        default=None, description="Reconstructed final domain state (None in validation-only)"
    )
    final_state_fingerprint: str = Field(
        description="Canonical SHA-256 fingerprint of final reconstructed state"
    )
    source_trace_hash: str | None = Field(
        default=None, description="Canonical execution trace hash from source, if available"
    )
    replay_trace_hash: str | None = Field(
        default=None, description="Canonical execution trace hash calculated from replayed trace"
    )
    source_result_hash: str | None = Field(
        default=None, description="Canonical backtest result hash from source, if available"
    )
    replay_result_hash: str | None = Field(
        default=None, description="Canonical backtest result hash calculated from replayed state"
    )
    checkpoints: tuple[ReplayCheckpoint, ...] = Field(
        default_factory=tuple, description="Deterministic checkpoints captured during replay"
    )
    first_divergence: ReplayMismatch | None = Field(
        default=None, description="Forensic details of first divergence if failed"
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple, description="Non-fatal warnings recorded during replay"
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple, description="Fatal errors recorded during replay"
    )
    duration_seconds: float = Field(
        default=0.0, ge=0.0, description="Observational elapsed time (NOT part of hash identity)"
    )
