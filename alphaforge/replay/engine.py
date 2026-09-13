"""
AlphaForge Replay Engine.
Authoritative offline replay subsystem.
Consumes previously recorded immutable audit events, verifies causal ordering,
hash chains, and FSM legality, reconstructs domain state, creates deterministic checkpoints,
and provides first-divergence diagnostics.
"""

import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from alphaforge.backtest.metrics import calculate_backtest_metrics
from alphaforge.backtest.models import (
    BacktestResult,
    BacktestTrade,
    QuantGateResult,
    QuantGateStatus,
    TraceEntry,
    TraceEventType,
    compute_result_canonical_hash,
    compute_trace_canonical_hash,
)
from alphaforge.core.exceptions import ReplayIntegrityError
from alphaforge.execution.enums import OrderState
from alphaforge.ledger.models import (
    GENESIS_PREVIOUS_HASH,
    AuditEvent,
    AuditEventType,
)
from alphaforge.replay.loader import ReplayArtifactSource
from alphaforge.replay.models import (
    ReplayCheckpoint,
    ReplayConfig,
    ReplayMismatch,
    ReplayMode,
    ReplayOrderState,
    ReplayPositionState,
    ReplayResult,
    ReplayState,
    ReplayStatus,
)
from alphaforge.replay.validation import (
    AuditIntegrityValidator,
    FSMTransitionValidator,
    ResultHashValidator,
    TraceHashValidator,
)
from alphaforge.risk.enums import TradeSide


class ReplayEngine:
    """
    Deterministic, audit-grade Replay Engine.
    Reconstructs complete operational and financial state from immutable audit events.
    """

    def __init__(
        self,
        config: ReplayConfig,
        source: ReplayArtifactSource,
        initial_checkpoint: ReplayCheckpoint | None = None,
    ) -> None:
        self._config = config
        self._source = source
        self._initial_checkpoint = initial_checkpoint
        self._last_exit_fill: dict[str, Any] | None = None

    @property
    def config(self) -> ReplayConfig:
        return self._config

    @property
    def source(self) -> ReplayArtifactSource:
        return self._source

    def replay(self) -> ReplayResult:
        """
        Execute replay in accordance with configured mode and invariants.
        Returns a deterministic ReplayResult.
        """
        start_wall_time = time.perf_counter()

        manifest = self._source.manifest
        replay_id = manifest.compute_replay_id()
        events = self._source.events

        # Handle empty source event stream
        if not events and self._initial_checkpoint is None:
            empty_state = ReplayState()
            empty_fp = empty_state.compute_fingerprint()
            elapsed = time.perf_counter() - start_wall_time
            return ReplayResult(
                status=ReplayStatus.WARNING,
                replay_id=replay_id,
                source_run_id=manifest.source_run_id,
                source_manifest=manifest,
                mode=self._config.mode,
                events_processed=0,
                events_verified=0,
                final_state=empty_state
                if self._config.mode != ReplayMode.VALIDATION_ONLY
                else None,
                final_state_fingerprint=empty_fp,
                checkpoints=(),
                first_divergence=None,
                warnings=("Replay executed on empty event stream",),
                errors=(),
                duration_seconds=elapsed,
            )

        # Initialize tracking structures
        seen_event_ids: dict[str, AuditEvent] = {}
        known_causal_ids: set[str] = {
            GENESIS_PREVIOUS_HASH,
            manifest.source_run_id,
        }
        checkpoints: list[ReplayCheckpoint] = []
        warnings: list[str] = []
        errors: list[str] = []

        # Setup state: fresh or restored from checkpoint
        if self._initial_checkpoint is not None:
            reconstructed_state = self._initial_checkpoint.state_snapshot.model_copy(deep=True)
            expected_sequence = self._initial_checkpoint.sequence_number + 1
            expected_previous_hash = (
                self._initial_checkpoint.state_snapshot.last_event_hash or GENESIS_PREVIOUS_HASH
            )
            previous_timestamp = self._initial_checkpoint.state_snapshot.last_event_timestamp
            checkpoints.append(self._initial_checkpoint)
            events_processed = self._initial_checkpoint.sequence_number
            events_verified = self._initial_checkpoint.sequence_number
        else:
            reconstructed_state = ReplayState(trace=(), trades=(), equity_curve=(), quant_gates=())
            expected_sequence = 1
            expected_previous_hash = GENESIS_PREVIOUS_HASH
            previous_timestamp = None
            events_processed = 0
            events_verified = 0

        first_divergence: ReplayMismatch | None = None

        for event in events:
            # Skip events already incorporated into resumed checkpoint
            if (
                self._initial_checkpoint is not None
                and event.sequence_number <= self._initial_checkpoint.sequence_number
            ):
                seen_event_ids[event.event_id] = event
                known_causal_ids.add(event.event_id)
                known_causal_ids.add(event.entity_id)
                if "order_id" in event.payload:
                    known_causal_ids.add(str(event.payload["order_id"]))
                if "signal_id" in event.payload:
                    known_causal_ids.add(str(event.payload["signal_id"]))
                continue

            # Prefix replay cutoff handling
            if self._config.mode == ReplayMode.PREFIX:
                if (
                    self._config.prefix_cutoff_sequence is not None
                    and event.sequence_number > self._config.prefix_cutoff_sequence
                ):
                    break
                if (
                    self._config.prefix_cutoff_timestamp is not None
                    and event.event_timestamp > self._config.prefix_cutoff_timestamp
                ):
                    break

            events_processed += 1

            # 1. Cryptographic and ledger integrity verification
            if self._config.verify_audit_hash_chain:
                integrity_mismatch = AuditIntegrityValidator.validate_event_integrity(
                    event=event,
                    expected_sequence=expected_sequence,
                    expected_previous_hash=expected_previous_hash,
                    previous_timestamp=previous_timestamp,
                    seen_event_ids=seen_event_ids,
                    known_causal_ids=known_causal_ids,
                )
                if integrity_mismatch is not None:
                    first_divergence = integrity_mismatch
                    errors.append(integrity_mismatch.diagnostic)
                    if self._config.fail_on_first_divergence:
                        elapsed = time.perf_counter() - start_wall_time
                        return ReplayResult(
                            status=ReplayStatus.FAIL,
                            replay_id=replay_id,
                            source_run_id=manifest.source_run_id,
                            source_manifest=manifest,
                            mode=self._config.mode,
                            events_processed=events_processed,
                            events_verified=events_verified,
                            final_state=reconstructed_state
                            if self._config.mode != ReplayMode.VALIDATION_ONLY
                            else None,
                            final_state_fingerprint=reconstructed_state.compute_fingerprint(),
                            checkpoints=tuple(checkpoints),
                            first_divergence=first_divergence,
                            warnings=tuple(warnings),
                            errors=tuple(errors),
                            duration_seconds=elapsed,
                        )

            # 2. Unknown event type validation (fail closed)
            try:
                # Validates that event_type is a recognized AuditEventType member
                _ = AuditEventType(event.event_type)
            except ValueError:
                first_divergence = ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="KNOWN_AUDIT_EVENT_TYPE",
                    replay_fingerprint=str(event.event_type),
                    mismatch_category="UNKNOWN_EVENT_TYPE",
                    diagnostic=(
                        f"Unknown audit event type '{event.event_type}' at sequence "
                        f"{event.sequence_number}. Replay engine fails closed."
                    ),
                )
                errors.append(first_divergence.diagnostic)
                elapsed = time.perf_counter() - start_wall_time
                return ReplayResult(
                    status=ReplayStatus.FAIL,
                    replay_id=replay_id,
                    source_run_id=manifest.source_run_id,
                    source_manifest=manifest,
                    mode=self._config.mode,
                    events_processed=events_processed,
                    events_verified=events_verified,
                    final_state=reconstructed_state
                    if self._config.mode != ReplayMode.VALIDATION_ONLY
                    else None,
                    final_state_fingerprint=reconstructed_state.compute_fingerprint(),
                    checkpoints=tuple(checkpoints),
                    first_divergence=first_divergence,
                    warnings=tuple(warnings),
                    errors=tuple(errors),
                    duration_seconds=elapsed,
                )

            # 3. Domain state reconstruction (if not validation-only)
            if self._config.mode != ReplayMode.VALIDATION_ONLY:
                state_before_fp = reconstructed_state.compute_fingerprint()

                transition_err = self._apply_event_transition(
                    event, reconstructed_state, state_before_fp
                )
                if transition_err is not None:
                    first_divergence = transition_err
                    errors.append(transition_err.diagnostic)
                    if self._config.fail_on_first_divergence:
                        elapsed = time.perf_counter() - start_wall_time
                        return ReplayResult(
                            status=ReplayStatus.FAIL,
                            replay_id=replay_id,
                            source_run_id=manifest.source_run_id,
                            source_manifest=manifest,
                            mode=self._config.mode,
                            events_processed=events_processed,
                            events_verified=events_verified,
                            final_state=reconstructed_state,
                            final_state_fingerprint=reconstructed_state.compute_fingerprint(),
                            checkpoints=tuple(checkpoints),
                            first_divergence=first_divergence,
                            warnings=tuple(warnings),
                            errors=tuple(errors),
                            duration_seconds=elapsed,
                        )

            # 4. Advance stream pointers
            seen_event_ids[event.event_id] = event
            known_causal_ids.add(event.event_id)
            known_causal_ids.add(event.entity_id)
            if "order_id" in event.payload:
                known_causal_ids.add(str(event.payload["order_id"]))
            if "signal_id" in event.payload:
                known_causal_ids.add(str(event.payload["signal_id"]))

            expected_sequence = event.sequence_number + 1
            expected_previous_hash = event.event_hash
            previous_timestamp = event.event_timestamp
            events_verified += 1

            # 5. Checkpoint capture at configured interval or trade boundary
            is_boundary = (
                event.sequence_number % self._config.checkpoint_interval == 0
                or event.event_type
                in (AuditEventType.TRADE_CLOSED, AuditEventType.BACKTEST_COMPLETED)
            )
            if is_boundary and self._config.mode != ReplayMode.VALIDATION_ONLY:
                cp = ReplayCheckpoint(
                    sequence_number=event.sequence_number,
                    event_id=event.event_id,
                    timestamp=event.event_timestamp,
                    state_fingerprint=reconstructed_state.compute_fingerprint(),
                    state_snapshot=reconstructed_state.model_copy(deep=True),
                )
                checkpoints.append(cp)

            # Stop if prefix event cutoff was reached
            if (
                self._config.mode == ReplayMode.PREFIX
                and self._config.prefix_cutoff_event_id is not None
                and event.event_id == self._config.prefix_cutoff_event_id
            ):
                break

        # 6. Post-replay canonical hash validations (Full mode)
        source_trace_hash = manifest.source_trace_canonical_hash
        replay_trace_hash: str | None = None
        if self._config.mode != ReplayMode.VALIDATION_ONLY:
            replay_trace_hash = compute_trace_canonical_hash(reconstructed_state.trace)

        if (
            self._config.verify_execution_trace
            and source_trace_hash is not None
            and replay_trace_hash is not None
        ):
            trace_mismatch = TraceHashValidator.validate(
                source_trace_hash=source_trace_hash,
                replay_trace_hash=replay_trace_hash,
                sequence_number=events_processed,
            )
            if trace_mismatch is not None:
                first_divergence = trace_mismatch
                errors.append(trace_mismatch.diagnostic)

        source_result_hash = manifest.source_result_canonical_hash
        replay_result_hash: str | None = None
        reconstructed_result: BacktestResult | None = None
        reconstruction_status = (
            "NO_SOURCE_RESULT" if self._source.backtest_result is None else "INCOMPLETE"
        )

        if (
            self._source.backtest_result is not None
            and self._config.mode != ReplayMode.VALIDATION_ONLY
        ):
            source_res = self._source.backtest_result
            # Independently compute performance metrics from reconstructed trades
            indep_metrics = calculate_backtest_metrics(
                trades=list(reconstructed_state.trades),
                equity_curve=source_res.equity_curve,
                initial_capital=source_res.config.initial_capital,
                start_time=source_res.config.start_time,
                end_time=source_res.config.end_time,
            )
            # Assemble combined execution trace with reconstructed audit trace entries
            audit_trace_types = {
                TraceEventType.SIGNAL_GENERATED,
                TraceEventType.RISK_EVALUATED,
                TraceEventType.ORDER_SIMULATED,
                TraceEventType.FILL_SIMULATED,
                TraceEventType.TRADE_CLOSED,
            }
            non_audit_trace = [
                e for e in source_res.execution_trace if e.event_type not in audit_trace_types
            ]
            combined_trace = tuple(
                sorted(
                    non_audit_trace + list(reconstructed_state.trace),
                    key=lambda e: e.timestamp,
                )
            )

            initial_reconstructed = BacktestResult(
                backtest_run_id=source_res.backtest_run_id,
                config=source_res.config,
                dataset_metadata=source_res.dataset_metadata,
                trades=tuple(reconstructed_state.trades),
                equity_curve=source_res.equity_curve,
                metrics=indep_metrics,
                validation_status=source_res.validation_status,
                quant_gates=source_res.quant_gates,
                diagnostics=dict(source_res.diagnostics),
                warnings=tuple(source_res.warnings),
                errors=tuple(source_res.errors),
                completion_status=source_res.completion_status,
                execution_trace=combined_trace,
            )
            # Compute canonical result hash strictly from reconstructed result
            replay_result_hash = compute_result_canonical_hash(initial_reconstructed)
            reconstructed_result = initial_reconstructed.model_copy(
                update={
                    "result_canonical_hash": replay_result_hash,
                    "trace_canonical_hash": compute_trace_canonical_hash(combined_trace),
                }
            )
            reconstruction_status = "COMPLETE"

            if self._config.verify_result_hash and source_result_hash is not None:
                result_mismatch = ResultHashValidator.validate(
                    source_result_hash=source_result_hash,
                    replay_result_hash=replay_result_hash,
                    sequence_number=events_processed,
                )
                if result_mismatch is not None:
                    first_divergence = result_mismatch
                    errors.append(result_mismatch.diagnostic)

        final_fp = (
            reconstructed_state.compute_fingerprint()
            if self._config.mode != ReplayMode.VALIDATION_ONLY
            else "VALIDATION_ONLY"
        )
        status = (
            ReplayStatus.FAIL
            if (errors or first_divergence is not None)
            else (ReplayStatus.WARNING if warnings else ReplayStatus.PASS)
        )
        elapsed = time.perf_counter() - start_wall_time

        return ReplayResult(
            status=status,
            replay_id=replay_id,
            source_run_id=manifest.source_run_id,
            source_manifest=manifest,
            mode=self._config.mode,
            events_processed=events_processed,
            events_verified=events_verified,
            final_state=reconstructed_state
            if self._config.mode != ReplayMode.VALIDATION_ONLY
            else None,
            final_state_fingerprint=final_fp,
            source_trace_hash=source_trace_hash,
            replay_trace_hash=replay_trace_hash,
            source_result_hash=source_result_hash,
            replay_result_hash=replay_result_hash,
            reconstructed_result=reconstructed_result,
            reconstruction_status=reconstruction_status,
            checkpoints=tuple(checkpoints),
            first_divergence=first_divergence,
            warnings=tuple(warnings),
            errors=tuple(errors),
            duration_seconds=elapsed,
        )

    def resume_from_checkpoint(self, checkpoint: ReplayCheckpoint) -> ReplayResult:
        """
        Resume replay execution deterministically from a previously captured checkpoint.
        Validates checkpoint integrity before resuming.
        Produces an identical final result and fingerprint as a fresh full replay.
        """
        if checkpoint.sequence_number < 1:
            raise ReplayIntegrityError(
                f"Invalid checkpoint sequence number: {checkpoint.sequence_number}"
            )
        snapshot_fp = checkpoint.state_snapshot.compute_fingerprint()
        if checkpoint.state_fingerprint != snapshot_fp:
            raise ReplayIntegrityError(
                f"Checkpoint state fingerprint mismatch: "
                f"recorded '{checkpoint.state_fingerprint}', computed '{snapshot_fp}'"
            )
        if checkpoint.sequence_number != checkpoint.state_snapshot.last_processed_sequence:
            raise ReplayIntegrityError(
                f"Checkpoint sequence number ({checkpoint.sequence_number}) does not match "
                f"snapshot last_processed_sequence "
                f"({checkpoint.state_snapshot.last_processed_sequence})"
            )

        engine = ReplayEngine(
            config=self._config,
            source=self._source,
            initial_checkpoint=checkpoint,
        )
        return engine.replay()

    def _resolve_symbol(self, event: AuditEvent | None = None) -> str:
        """Deterministically resolve market symbol from event payload, manifest, or result."""
        if event is not None and "symbol" in event.payload:
            sym = str(event.payload["symbol"]).strip()
            if sym:
                return sym
        if self._source.manifest.source_contract_id:
            return self._source.manifest.source_contract_id
        if self._source.backtest_result is not None:
            return self._source.backtest_result.dataset_metadata.symbol
        return "UNKNOWN"

    @staticmethod
    def _with_state_before(
        mismatch: ReplayMismatch | None, state_before_fp: str | None
    ) -> ReplayMismatch | None:
        if mismatch is not None and state_before_fp is not None:
            return mismatch.model_copy(
                update={
                    "source_state_before": state_before_fp,
                    "replay_state_before": state_before_fp,
                }
            )
        return mismatch

    def _apply_event_transition(
        self,
        event: AuditEvent,
        state: ReplayState,
        state_before_fp: str | None = None,
    ) -> ReplayMismatch | None:
        """
        Apply a deterministic state transition for a single audit event.
        Mutates ReplayState in-place (which is isolated to the replay execution).
        Returns None on success or ReplayMismatch on failure.
        """
        payload = event.payload
        ts = event.event_timestamp

        # Check contract expiry on every event timestamp
        if (
            state.contract.expiry_datetime is not None
            and ts >= state.contract.expiry_datetime
            and not state.contract.is_expired
        ):
            object.__setattr__(
                state,
                "contract",
                state.contract.model_copy(update={"is_expired": True}),
            )

        if event.event_type == AuditEventType.BACKTEST_STARTED:
            cap_str = str(payload.get("initial_capital", "1000000"))
            initial_cap = Decimal(cap_str)
            contract_id = payload.get("contract_id")
            exp_str = payload.get("expiry_datetime") or payload.get("expiry_timestamp")
            exp_dt: datetime | None = None
            if exp_str:
                exp_dt = (
                    datetime.fromisoformat(str(exp_str)) if isinstance(exp_str, str) else exp_str
                )
            if contract_id or exp_dt:
                cid = str(contract_id) if contract_id else state.contract.contract_id
                object.__setattr__(
                    state,
                    "contract",
                    state.contract.model_copy(
                        update={
                            "contract_id": cid,
                            "expiry_datetime": exp_dt or state.contract.expiry_datetime,
                        }
                    ),
                )
            object.__setattr__(
                state,
                "portfolio",
                state.portfolio.model_copy(
                    update={
                        "initial_capital": initial_cap,
                        "cash": initial_cap,
                        "equity": initial_cap,
                    }
                ),
            )

        elif event.event_type == AuditEventType.SIGNAL_GENERATED:
            sig_id = str(payload.get("signal_id", event.entity_id))
            dir_str = str(payload.get("direction", "LONG")).upper()
            side = TradeSide.LONG if dir_str in ("LONG", "BUY") else TradeSide.SHORT
            symbol = self._resolve_symbol(event)
            entry_ref_str = payload.get("entry_price")
            entry_ref = Decimal(str(entry_ref_str)) if entry_ref_str is not None else None
            stop_ref = str(payload.get("stop_loss", ""))
            target_ref = str(payload.get("profit_target", ""))

            trace_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.SIGNAL_GENERATED,
                entity_id=sig_id,
                symbol=symbol,
                side=side,
                price=entry_ref,
                reason="STRATEGY_ACCEPT",
                metadata={
                    "direction": dir_str,
                    "stop": stop_ref,
                    "target": target_ref,
                },
            )
            object.__setattr__(state, "trace", state.trace + (trace_entry,))

        elif event.event_type in (AuditEventType.ORDER_SIMULATED, AuditEventType.ORDER_CREATED):
            order_id = str(payload.get("order_id", event.entity_id))
            side_str = str(payload.get("side", "LONG"))
            side = TradeSide.LONG if side_str.upper() in ("BUY", "LONG") else TradeSide.SHORT
            qty = int(payload.get("quantity", 1))
            symbol = self._resolve_symbol(event)
            signal_id = str(payload.get("signal_id", event.causation_id or "")) or None

            order_price: Decimal | None = None
            if "entry_price" in payload:
                order_price = Decimal(str(payload["entry_price"]))
            elif "price" in payload:
                order_price = Decimal(str(payload["price"]))
            else:
                for te in reversed(state.trace):
                    if te.event_type == TraceEventType.SIGNAL_GENERATED and (
                        te.entity_id == signal_id or te.entity_id == event.causation_id
                    ):
                        order_price = te.price
                        break

            order_state = ReplayOrderState(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=qty,
                state=OrderState.SUBMITTED,
                signal_id=signal_id,
                created_at=ts,
                updated_at=ts,
                history_event_types=(event.event_type.value,),
            )
            state.orders[order_id] = order_state

            trace_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.ORDER_SIMULATED,
                entity_id=order_id,
                symbol=symbol,
                side=side,
                quantity=qty,
                price=order_price,
                effective_price=None,
                reason="MARKET_ORDER_QUEUED",
                state="SUBMITTED",
                metadata={"signal_id": str(signal_id or "")},
            )
            object.__setattr__(state, "trace", state.trace + (trace_entry,))

        elif event.event_type in (
            AuditEventType.ORDER_VALIDATED,
            AuditEventType.ORDER_SUBMITTED,
            AuditEventType.ORDER_ACKNOWLEDGED,
        ):
            order_id = event.entity_id
            target_state = (
                OrderState.VALIDATED
                if event.event_type == AuditEventType.ORDER_VALIDATED
                else (
                    OrderState.SUBMITTED
                    if event.event_type == AuditEventType.ORDER_SUBMITTED
                    else OrderState.ACKNOWLEDGED
                )
            )
            if order_id in state.orders:
                curr_ord = state.orders[order_id]
                if self._config.verify_fsm_transitions:
                    fsm_mismatch = FSMTransitionValidator.validate_transition(
                        order_id=order_id,
                        current_state=curr_ord.state,
                        target_state=target_state,
                        sequence_number=event.sequence_number,
                        event_id=event.event_id,
                    )
                    if fsm_mismatch is not None:
                        return self._with_state_before(fsm_mismatch, state_before_fp)

                state.orders[order_id] = curr_ord.model_copy(
                    update={
                        "state": target_state,
                        "updated_at": ts,
                        "history_event_types": curr_ord.history_event_types
                        + (event.event_type.value,),
                    }
                )

        elif event.event_type in (AuditEventType.ORDER_FILLED, AuditEventType.FILL_SIMULATED):
            order_id = str(payload.get("order_id", event.entity_id))
            fill_type = str(payload.get("fill_type", "MARKET"))
            eff_price = Decimal(str(payload.get("effective_price", "0")))
            fee = Decimal(str(payload.get("fee", "0")))
            slippage = Decimal(str(payload.get("slippage_loss", "0")))

            if not state.position.has_open_position:
                # ENTRY FILL
                raw_side = payload.get("side")
                if raw_side is not None:
                    side_str = str(raw_side)
                    side = (
                        TradeSide.LONG if side_str.upper() in ("BUY", "LONG") else TradeSide.SHORT
                    )
                elif order_id in state.orders:
                    side = state.orders[order_id].side
                else:
                    side = TradeSide.LONG

                qty = (
                    int(payload["quantity"])
                    if "quantity" in payload and payload["quantity"] is not None
                    else (state.orders[order_id].quantity if order_id in state.orders else 1)
                )
                symbol = self._resolve_symbol(event)

                if order_id in state.orders:
                    curr_ord = state.orders[order_id]
                    if self._config.verify_fsm_transitions:
                        if curr_ord.state == OrderState.SUBMITTED:
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=curr_ord.state,
                                target_state=OrderState.ACKNOWLEDGED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=OrderState.ACKNOWLEDGED,
                                target_state=OrderState.FILLED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)
                        else:
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=curr_ord.state,
                                target_state=OrderState.FILLED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)

                    state.orders[order_id] = curr_ord.model_copy(
                        update={
                            "state": OrderState.FILLED,
                            "filled_quantity": curr_ord.filled_quantity + qty,
                            "average_price": eff_price,
                            "updated_at": ts,
                            "history_event_types": curr_ord.history_event_types
                            + (event.event_type.value,),
                        }
                    )

                if state.contract.is_expired:
                    object.__setattr__(
                        state,
                        "contract",
                        state.contract.model_copy(
                            update={
                                "post_expiry_fills": state.contract.post_expiry_fills + 1,
                                "lifecycle_violation": True,
                            }
                        ),
                    )

                notional = eff_price * Decimal(qty) * state.contract.contract_multiplier
                sig_id = str(payload.get("signal_id", event.causation_id or order_id))
                new_pos = ReplayPositionState(
                    has_open_position=True,
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    entry_reference_price=eff_price,
                    entry_effective_price=eff_price,
                    entry_timestamp=ts,
                    entry_signal_id=sig_id,
                    strategy_version="1.0.0",
                    entry_fee=fee,
                    entry_slippage=slippage,
                    notional_exposure=notional,
                    margin_used=Decimal("0"),
                )
                object.__setattr__(state, "position", new_pos)
                object.__setattr__(
                    state,
                    "portfolio",
                    state.portfolio.model_copy(
                        update={
                            "cumulative_fees": state.portfolio.cumulative_fees + fee,
                            "cumulative_slippage": state.portfolio.cumulative_slippage + slippage,
                            "cash": state.portfolio.cash - fee - slippage,
                            "equity": state.portfolio.equity - fee - slippage,
                        }
                    ),
                )

                trace_entry = TraceEntry(
                    timestamp=ts,
                    event_type=TraceEventType.FILL_SIMULATED,
                    entity_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    price=eff_price,
                    effective_price=eff_price,
                    reason=fill_type,
                    state="FILLED",
                    metadata={
                        "fee": str(fee),
                        "slippage_loss": str(slippage),
                    },
                )
                object.__setattr__(state, "trace", state.trace + (trace_entry,))

            else:
                # EXIT FILL
                pos = state.position
                exit_fill_data = {
                    "order_id": order_id,
                    "fill_type": fill_type,
                    "effective_price": str(eff_price),
                    "fee": str(fee),
                    "slippage_loss": str(slippage),
                    "timestamp": ts.isoformat(),
                }
                object.__setattr__(state, "last_exit_fill", exit_fill_data)

                if order_id in state.orders:
                    curr_ord = state.orders[order_id]
                    if self._config.verify_fsm_transitions:
                        if curr_ord.state == OrderState.SUBMITTED:
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=curr_ord.state,
                                target_state=OrderState.ACKNOWLEDGED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=OrderState.ACKNOWLEDGED,
                                target_state=OrderState.FILLED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)
                        else:
                            fsm_mismatch = FSMTransitionValidator.validate_transition(
                                order_id=order_id,
                                current_state=curr_ord.state,
                                target_state=OrderState.FILLED,
                                sequence_number=event.sequence_number,
                                event_id=event.event_id,
                            )
                            if fsm_mismatch is not None:
                                return self._with_state_before(fsm_mismatch, state_before_fp)

                    state.orders[order_id] = curr_ord.model_copy(
                        update={
                            "state": OrderState.FILLED,
                            "filled_quantity": curr_ord.filled_quantity + pos.quantity,
                            "average_price": eff_price,
                            "updated_at": ts,
                            "history_event_types": curr_ord.history_event_types
                            + (event.event_type.value,),
                        }
                    )

                trace_entry = TraceEntry(
                    timestamp=ts,
                    event_type=TraceEventType.FILL_SIMULATED,
                    entity_id=order_id,
                    symbol=pos.symbol,
                    side=pos.side,
                    quantity=pos.quantity,
                    price=eff_price,
                    effective_price=eff_price,
                    reason=fill_type,
                    state="FILLED",
                    metadata={
                        "fee": str(fee),
                        "slippage_loss": str(slippage),
                    },
                )
                object.__setattr__(state, "trace", state.trace + (trace_entry,))

        elif event.event_type == AuditEventType.TRADE_CLOSED:
            if not state.position.has_open_position:
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="OPEN_POSITION_EXPECTED",
                    replay_fingerprint="NO_OPEN_POSITION",
                    mismatch_category="TRADE_RECONSTRUCTION_ERROR",
                    diagnostic=(
                        f"Cannot reconstruct TRADE_CLOSED at seq {event.sequence_number}: "
                        "no open position"
                    ),
                )

            pos = state.position
            exit_fill = dict(state.last_exit_fill) if state.last_exit_fill else {}

            trade_id = str(payload.get("trade_id") or event.entity_id or "")
            if not trade_id or trade_id == "UNKNOWN":
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="VALID_TRADE_ID",
                    replay_fingerprint=trade_id,
                    mismatch_category="MISSING_TRADE_DATA",
                    diagnostic=f"Missing trade_id for TRADE_CLOSED at seq {event.sequence_number}",
                )
            exit_fee = Decimal(str(exit_fill.get("fee", payload.get("exit_fee", "0"))))
            exit_slippage = Decimal(
                str(exit_fill.get("slippage_loss", payload.get("exit_slippage", "0")))
            )
            tot_fees = pos.entry_fee + exit_fee
            tot_slippage = pos.entry_slippage + exit_slippage

            exit_price_val = exit_fill.get("effective_price") or payload.get("exit_price")
            if exit_price_val is not None:
                exit_price = Decimal(str(exit_price_val))
            elif "gross_pnl" in payload or "net_pnl" in payload:
                qty_dec = Decimal(pos.quantity) if pos.quantity > 0 else Decimal("1")
                mult = (
                    state.contract.contract_multiplier
                    if state.contract.contract_multiplier > 0
                    else Decimal("1")
                )
                if "gross_pnl" in payload:
                    g_pnl = Decimal(str(payload["gross_pnl"]))
                else:
                    g_pnl = Decimal(str(payload["net_pnl"])) + tot_fees + tot_slippage
                price_diff = g_pnl / (qty_dec * mult)
                exit_price = (
                    pos.entry_reference_price + price_diff
                    if pos.side == TradeSide.LONG
                    else pos.entry_reference_price - price_diff
                )
            else:
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="VALID_EXIT_PRICE",
                    replay_fingerprint="MISSING_EXIT_PRICE",
                    mismatch_category="MISSING_TRADE_DATA",
                    diagnostic=f"Missing exit price for trade {trade_id}",
                )

            if exit_price <= Decimal("0"):
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="POSITIVE_EXIT_PRICE",
                    replay_fingerprint=str(exit_price),
                    mismatch_category="MISSING_TRADE_DATA",
                    diagnostic=f"Non-positive exit price {exit_price} for trade {trade_id}",
                )

            if (
                not pos.symbol
                or pos.symbol == "UNKNOWN"
                or pos.side is None
                or pos.entry_timestamp is None
                or pos.quantity <= 0
                or pos.entry_effective_price <= Decimal("0")
                or not pos.entry_signal_id
            ):
                return ReplayMismatch(
                    divergent_sequence=event.sequence_number,
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source_fingerprint="COMPLETE_POSITION_DATA",
                    replay_fingerprint="MISSING_POSITION_DATA",
                    mismatch_category="MISSING_TRADE_DATA",
                    diagnostic=f"Incomplete open position state for trade {trade_id}",
                )

            exit_fee = Decimal(str(exit_fill.get("fee", payload.get("exit_fee", "0"))))
            exit_slippage = Decimal(
                str(exit_fill.get("slippage_loss", payload.get("exit_slippage", "0")))
            )
            tot_fees = pos.entry_fee + exit_fee
            tot_slippage = pos.entry_slippage + exit_slippage

            dec_qty = Decimal(pos.quantity)
            if "gross_pnl" in payload:
                gross_pnl = Decimal(str(payload["gross_pnl"]))
            else:
                gross_pnl = (
                    (exit_price - pos.entry_reference_price)
                    * dec_qty
                    * state.contract.contract_multiplier
                    if pos.side == TradeSide.LONG
                    else (pos.entry_reference_price - exit_price)
                    * dec_qty
                    * state.contract.contract_multiplier
                )

            if "net_pnl" in payload:
                net_pnl = Decimal(str(payload["net_pnl"]))
            else:
                net_pnl = gross_pnl - (tot_fees + tot_slippage)

            entry_notional = (
                pos.entry_effective_price * dec_qty * state.contract.contract_multiplier
            )
            return_pct = (
                (net_pnl / entry_notional) * Decimal("100")
                if entry_notional > Decimal("0")
                else Decimal("0")
            )
            duration_seconds = max(0, int((ts - pos.entry_timestamp).total_seconds()))
            exit_reason = str(payload.get("exit_reason", "SIGNAL"))
            is_forced = exit_reason in ("CONTRACT_EXPIRED", "END_OF_TEST_FORCED_CLOSE")

            trade = BacktestTrade(
                trade_id=trade_id,
                symbol=pos.symbol,
                side=pos.side,
                entry_timestamp=pos.entry_timestamp,
                entry_price=pos.entry_effective_price,
                entry_quantity=pos.quantity,
                exit_timestamp=ts,
                exit_price=exit_price,
                exit_quantity=pos.quantity,
                gross_pnl=gross_pnl,
                fees=tot_fees,
                slippage=tot_slippage,
                other_costs=Decimal("0"),
                net_pnl=net_pnl,
                return_pct=return_pct,
                holding_duration_seconds=duration_seconds,
                max_favorable_excursion=Decimal("0"),
                max_adverse_excursion=Decimal("0"),
                entry_signal_id=pos.entry_signal_id,
                strategy_version=pos.strategy_version or "1.0.0",
                exit_reason=exit_reason,
                is_forced_close=is_forced,
            )

            object.__setattr__(state, "trades", state.trades + (trade,))
            object.__setattr__(
                state,
                "portfolio",
                state.portfolio.model_copy(
                    update={
                        "cumulative_realized_pnl": state.portfolio.cumulative_realized_pnl
                        + gross_pnl,
                        "cumulative_fees": state.portfolio.cumulative_fees + exit_fee,
                        "cumulative_slippage": state.portfolio.cumulative_slippage + exit_slippage,
                        "cash": state.portfolio.cash + gross_pnl - exit_slippage - exit_fee,
                        "equity": state.portfolio.equity + net_pnl,
                        "completed_trades_count": state.portfolio.completed_trades_count + 1,
                    }
                ),
            )
            object.__setattr__(state, "position", ReplayPositionState(has_open_position=False))
            object.__setattr__(state, "last_exit_fill", None)

            trace_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.TRADE_CLOSED,
                entity_id=trade_id,
                symbol=trade.symbol,
                side=trade.side,
                quantity=trade.exit_quantity,
                price=trade.entry_price,
                effective_price=trade.exit_price,
                reason=exit_reason,
                state="CLOSED",
                metadata={
                    "gross_pnl": str(gross_pnl),
                    "net_pnl": str(net_pnl),
                },
            )
            object.__setattr__(state, "trace", state.trace + (trace_entry,))

        elif event.event_type == AuditEventType.ORDER_CANCELLED:
            order_id = event.entity_id
            if order_id in state.orders:
                curr_ord = state.orders[order_id]
                if self._config.verify_fsm_transitions:
                    fsm_mismatch = FSMTransitionValidator.validate_transition(
                        order_id=order_id,
                        current_state=curr_ord.state,
                        target_state=OrderState.CANCELLED,
                        sequence_number=event.sequence_number,
                        event_id=event.event_id,
                    )
                    if fsm_mismatch is not None:
                        return self._with_state_before(fsm_mismatch, state_before_fp)
                state.orders[order_id] = curr_ord.model_copy(
                    update={
                        "state": OrderState.CANCELLED,
                        "updated_at": ts,
                        "history_event_types": curr_ord.history_event_types
                        + (event.event_type.value,),
                    }
                )

        elif event.event_type == AuditEventType.ORDER_REJECTED:
            order_id = event.entity_id
            if order_id in state.orders:
                curr_ord = state.orders[order_id]
                if self._config.verify_fsm_transitions:
                    fsm_mismatch = FSMTransitionValidator.validate_transition(
                        order_id=order_id,
                        current_state=curr_ord.state,
                        target_state=OrderState.REJECTED,
                        sequence_number=event.sequence_number,
                        event_id=event.event_id,
                    )
                    if fsm_mismatch is not None:
                        return self._with_state_before(fsm_mismatch, state_before_fp)
                state.orders[order_id] = curr_ord.model_copy(
                    update={
                        "state": OrderState.REJECTED,
                        "updated_at": ts,
                        "history_event_types": curr_ord.history_event_types
                        + (event.event_type.value,),
                    }
                )

        elif event.event_type in (
            AuditEventType.PROTECTION_PENDING,
            AuditEventType.PROTECTION_CONFIRMED,
        ):
            order_id = event.entity_id
            target_state = (
                OrderState.PROTECTION_PENDING
                if event.event_type == AuditEventType.PROTECTION_PENDING
                else OrderState.PROTECTED
            )
            if order_id in state.orders:
                curr_ord = state.orders[order_id]
                if self._config.verify_fsm_transitions:
                    fsm_mismatch = FSMTransitionValidator.validate_transition(
                        order_id=order_id,
                        current_state=curr_ord.state,
                        target_state=target_state,
                        sequence_number=event.sequence_number,
                        event_id=event.event_id,
                    )
                    if fsm_mismatch is not None:
                        return self._with_state_before(fsm_mismatch, state_before_fp)
                state.orders[order_id] = curr_ord.model_copy(
                    update={
                        "state": target_state,
                        "is_protection_confirmed": target_state == OrderState.PROTECTED,
                        "updated_at": ts,
                        "history_event_types": curr_ord.history_event_types
                        + (event.event_type.value,),
                    }
                )

        elif event.event_type == AuditEventType.RISK_CHECK:
            object.__setattr__(
                state,
                "risk",
                state.risk.model_copy(
                    update={
                        "risk_evaluations": state.risk.risk_evaluations + 1,
                        "risk_approvals": state.risk.risk_approvals + 1,
                    }
                ),
            )

        elif event.event_type == AuditEventType.RISK_REJECTED:
            object.__setattr__(
                state,
                "risk",
                state.risk.model_copy(
                    update={
                        "risk_evaluations": state.risk.risk_evaluations + 1,
                        "risk_rejections": state.risk.risk_rejections + 1,
                    }
                ),
            )

        elif event.event_type == AuditEventType.CIRCUIT_BREAKER_TRIGGERED:
            object.__setattr__(
                state,
                "risk",
                state.risk.model_copy(update={"circuit_breaker_active": True}),
            )

        elif event.event_type in (
            AuditEventType.VALIDATION_FAILED,
            AuditEventType.VALIDATION_WARNING,
        ):
            status = (
                QuantGateStatus.FAIL
                if event.event_type == AuditEventType.VALIDATION_FAILED
                else QuantGateStatus.WARNING
            )
            evidence = payload.get("evidence")
            evidence_dict = dict(evidence) if isinstance(evidence, dict) else {}
            gate = QuantGateResult(
                gate_id=str(payload.get("gate_id", "GATE_UNKNOWN")),
                gate_name=str(payload.get("gate_name", "UNKNOWN")),
                status=status,
                reason=str(payload.get("reason", "")),
                evidence=evidence_dict,
            )
            object.__setattr__(state, "quant_gates", state.quant_gates + (gate,))

        # Update last processed sequence, hash, and timestamp
        object.__setattr__(state, "last_processed_sequence", event.sequence_number)
        object.__setattr__(state, "last_event_id", event.event_id)
        object.__setattr__(state, "last_event_hash", event.event_hash)
        object.__setattr__(state, "last_event_timestamp", ts)

        return None
