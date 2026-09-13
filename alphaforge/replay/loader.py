"""
AlphaForge Replay Engine Artifact Loader.
Provides non-invasive, read-only ingestion of historical audit events, ledgers,
and backtest execution artifacts.
Guarantees source artifact immutability and deterministic manifest extraction.
"""

from collections.abc import Sequence
from pathlib import Path

from alphaforge.backtest.models import (
    BacktestResult,
    TraceEntry,
    TraceEventType,
    compute_trace_canonical_hash,
)
from alphaforge.core.exceptions import ReplayIntegrityError
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEvent, AuditEventType
from alphaforge.ledger.storage import AbstractLedgerStorage, FileLedgerStorage
from alphaforge.replay.models import (
    REPLAY_ENGINE_VERSION,
    REPLAY_SCHEMA_VERSION,
    ReplayManifest,
)


class ReplayArtifactSource:
    """
    Encapsulates source artifacts for replay execution without mutating them.
    Reads events from an AuditLedger, storage engine, event list, or JSONL file.
    """

    def __init__(
        self,
        events: Sequence[AuditEvent] | None = None,
        trace: Sequence[TraceEntry] | None = None,
        ledger: AuditLedger | None = None,
        storage: AbstractLedgerStorage | None = None,
        file_path: Path | str | None = None,
        backtest_result: BacktestResult | None = None,
        manifest: ReplayManifest | None = None,
    ) -> None:
        self._backtest_result = backtest_result
        self._explicit_manifest = manifest

        # Resolve trace immutably
        if trace is not None:
            self._trace: tuple[TraceEntry, ...] = tuple(trace)
        elif backtest_result is not None:
            self._trace = tuple(backtest_result.execution_trace)
        else:
            self._trace = ()

        # Resolve events immutably
        if events is not None:
            self._events: tuple[AuditEvent, ...] = tuple(events)
        elif ledger is not None:
            # Read from ledger without touching or modifying its internal state
            self._events = tuple(ledger.get_events_by_correlation_id("")) or tuple(
                # Fallback: query from sequence 1 upwards
                self._read_ledger_events(ledger)
            )
        elif storage is not None:
            self._events = tuple(storage.read_all())
        elif file_path is not None:
            fl_storage = FileLedgerStorage(file_path)
            self._events = tuple(fl_storage.read_all())
        else:
            self._events = ()

        # Resolve or extract manifest
        self._manifest = self._resolve_manifest()

        # Validate schema version compatibility
        if self._manifest.source_replay_schema_version != REPLAY_SCHEMA_VERSION:
            raise ReplayIntegrityError(
                f"Incompatible replay schema version: expected '{REPLAY_SCHEMA_VERSION}', "
                f"got '{self._manifest.source_replay_schema_version}'"
            )

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        """Return immutable tuple of loaded audit events."""
        return self._events

    @property
    def manifest(self) -> ReplayManifest:
        """Return authoritative replay manifest."""
        return self._manifest

    @property
    def backtest_result(self) -> BacktestResult | None:
        """Return associated backtest result if loaded."""
        return self._backtest_result

    @property
    def trace(self) -> tuple[TraceEntry, ...]:
        """Return immutable tuple of loaded execution trace entries."""
        return self._trace

    @staticmethod
    def _read_ledger_events(ledger: AuditLedger) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        seq = 1
        while True:
            ev = ledger.get_event_by_sequence(seq)
            if ev is None:
                break
            events.append(ev)
            seq += 1
        return events

    def _resolve_manifest(self) -> ReplayManifest:
        if self._explicit_manifest is not None:
            return self._explicit_manifest

        # If BacktestResult is provided, extract high-fidelity manifest
        if self._backtest_result is not None:
            res = self._backtest_result
            auth_trace = [
                e
                for e in res.execution_trace
                if e.event_type
                in (
                    TraceEventType.SIGNAL_GENERATED,
                    TraceEventType.RISK_EVALUATED,
                    TraceEventType.ORDER_SIMULATED,
                    TraceEventType.FILL_SIMULATED,
                    TraceEventType.TRADE_CLOSED,
                )
            ]
            return ReplayManifest(
                source_run_id=res.backtest_run_id,
                source_dataset_id=res.dataset_metadata.dataset_id,
                source_dataset_checksum=res.dataset_metadata.checksum,
                source_strategy_id=res.config.strategy_id,
                source_strategy_version=res.config.strategy_version,
                source_engine_version=res.config.engine_version,
                source_result_canonical_hash=res.result_canonical_hash,
                source_trace_canonical_hash=compute_trace_canonical_hash(auth_trace),
                source_contract_id=res.dataset_metadata.symbol,
                source_risk_config_fingerprint=None,
                source_cost_config_fingerprint=None,
                source_replay_schema_version=REPLAY_SCHEMA_VERSION,
                replay_engine_version=REPLAY_ENGINE_VERSION,
            )

        source_trace_hash: str | None = None
        if self._trace:
            auth_trace = [
                e
                for e in self._trace
                if e.event_type
                in (
                    TraceEventType.SIGNAL_GENERATED,
                    TraceEventType.RISK_EVALUATED,
                    TraceEventType.ORDER_SIMULATED,
                    TraceEventType.FILL_SIMULATED,
                    TraceEventType.TRADE_CLOSED,
                )
            ]
            source_trace_hash = compute_trace_canonical_hash(auth_trace)

        # Inspect events for BACKTEST_STARTED event
        for ev in self._events:
            if ev.event_type == AuditEventType.BACKTEST_STARTED:
                payload = ev.payload
                return ReplayManifest(
                    source_run_id=str(payload.get("run_id", ev.entity_id)),
                    source_dataset_id=str(payload.get("dataset_id", "DATASET_UNKNOWN")),
                    source_dataset_checksum=str(
                        payload.get("dataset_checksum", "CHECKSUM_UNKNOWN")
                    ),
                    source_strategy_id=str(payload.get("strategy_id", "STRATEGY_UNKNOWN")),
                    source_strategy_version=str(payload.get("strategy_version", "1.0.0")),
                    source_engine_version="1.0.0",
                    source_result_canonical_hash=None,
                    source_trace_canonical_hash=source_trace_hash,
                    source_contract_id=None,
                    source_risk_config_fingerprint=None,
                    source_cost_config_fingerprint=None,
                    source_replay_schema_version=REPLAY_SCHEMA_VERSION,
                    replay_engine_version=REPLAY_ENGINE_VERSION,
                )

        # Default fallback manifest for standalone audit event streams
        first_run_id = self._events[0].correlation_id if self._events else "RUN_EMPTY"
        return ReplayManifest(
            source_run_id=first_run_id,
            source_dataset_id="DATASET_DEFAULT",
            source_dataset_checksum="CHECKSUM_DEFAULT",
            source_strategy_id="STRATEGY_DEFAULT",
            source_strategy_version="1.0.0",
            source_engine_version="1.0.0",
            source_result_canonical_hash=None,
            source_trace_canonical_hash=source_trace_hash,
            source_contract_id=None,
            source_risk_config_fingerprint=None,
            source_cost_config_fingerprint=None,
            source_replay_schema_version=REPLAY_SCHEMA_VERSION,
            replay_engine_version=REPLAY_ENGINE_VERSION,
        )
