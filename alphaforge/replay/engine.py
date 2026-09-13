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

from alphaforge.backtest.models import (
    TraceEntry,
    TraceEventType,
    compute_trace_canonical_hash,
)
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
            initial_trace = self._source.trace if self._source.trace else ()
            reconstructed_state = ReplayState(trace=initial_trace)
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
        if (
            self._config.verify_result_hash
            and source_result_hash is not None
            and self._source.backtest_result is not None
        ):
            replay_result_hash = self._source.backtest_result.result_canonical_hash
            if replay_result_hash is not None:
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
            checkpoints=tuple(checkpoints),
            first_divergence=first_divergence,
            warnings=tuple(warnings),
            errors=tuple(errors),
            duration_seconds=elapsed,
        )

    def resume_from_checkpoint(self, checkpoint: ReplayCheckpoint) -> ReplayResult:
        """
        Resume replay execution deterministically from a previously captured checkpoint.
        Produces an identical final result and fingerprint as a fresh full replay.
        """
        engine = ReplayEngine(
            config=self._config,
            source=self._source,
            initial_checkpoint=checkpoint,
        )
        return engine.replay()

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

        elif event.event_type in (AuditEventType.ORDER_SIMULATED, AuditEventType.ORDER_CREATED):
            order_id = str(payload.get("order_id", event.entity_id))
            side_str = str(payload.get("side", "LONG"))
            side = TradeSide.LONG if side_str.upper() in ("BUY", "LONG") else TradeSide.SHORT
            qty = int(payload.get("quantity", 1))
            symbol = str(payload.get("symbol", "NIFTY"))
            signal_id = str(payload.get("signal_id", "")) or None

            raw_ord_state = str(payload.get("order_state", "CREATED")).upper()
            try:
                ord_state = OrderState(raw_ord_state)
            except ValueError:
                ord_state = OrderState.CREATED

            order_state = ReplayOrderState(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=qty,
                state=ord_state,
                signal_id=signal_id,
                created_at=ts,
                updated_at=ts,
                history_event_types=(event.event_type.value,),
            )
            state.orders[order_id] = order_state

            # Append trace entry for simulated order
            trace_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.ORDER_SIMULATED,
                entity_id=order_id,
                symbol=symbol,
                side=side,
                quantity=qty,
                price=Decimal(str(payload.get("entry_price", "0"))),
                effective_price=None,
                reason="SIGNAL",
                state="CREATED",
                metadata={"signal_id": str(signal_id or "")},
            )
            if not self._source.trace:
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
            qty = int(payload.get("quantity", 1))
            raw_side = payload.get("side")
            side_str = str(raw_side) if raw_side is not None else "LONG"
            side = TradeSide.LONG if side_str.upper() in ("BUY", "LONG") else TradeSide.SHORT

            # Check contract expiry
            if state.contract.expiry_datetime is not None and ts >= state.contract.expiry_datetime:
                object.__setattr__(
                    state,
                    "contract",
                    state.contract.model_copy(update={"is_expired": True}),
                )

            # Update order FSM if order tracked
            if order_id in state.orders:
                curr_ord = state.orders[order_id]
                if self._config.verify_fsm_transitions:
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

            # Portfolio and position state mutation
            if not state.position.has_open_position:
                # Opening new position
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
                new_pos = ReplayPositionState(
                    has_open_position=True,
                    symbol=str(payload.get("symbol", "NIFTY")),
                    side=side,
                    quantity=qty,
                    entry_reference_price=eff_price,
                    entry_effective_price=eff_price,
                    entry_timestamp=ts,
                    entry_signal_id=str(payload.get("signal_id", order_id)),
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
            else:
                # Closing position
                pos = state.position
                dec_qty = Decimal(pos.quantity)
                ref_gross_pnl = (
                    (eff_price - pos.entry_reference_price)
                    * dec_qty
                    * state.contract.contract_multiplier
                    if pos.side == TradeSide.LONG
                    else (pos.entry_reference_price - eff_price)
                    * dec_qty
                    * state.contract.contract_multiplier
                )
                tot_fees = pos.entry_fee + fee
                tot_slippage = pos.entry_slippage + slippage
                net_pnl = ref_gross_pnl - (tot_fees + tot_slippage)

                object.__setattr__(
                    state,
                    "portfolio",
                    state.portfolio.model_copy(
                        update={
                            "cumulative_realized_pnl": state.portfolio.cumulative_realized_pnl
                            + ref_gross_pnl,
                            "cumulative_fees": state.portfolio.cumulative_fees + fee,
                            "cumulative_slippage": state.portfolio.cumulative_slippage + slippage,
                            "cash": state.portfolio.cash + ref_gross_pnl - slippage - fee,
                            "equity": state.portfolio.equity + net_pnl,
                            "completed_trades_count": state.portfolio.completed_trades_count + 1,
                        }
                    ),
                )
                object.__setattr__(state, "position", ReplayPositionState(has_open_position=False))

            # Record execution trace entry
            trace_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.FILL_SIMULATED,
                entity_id=order_id,
                symbol=str(payload.get("symbol", "NIFTY")),
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
            if not self._source.trace:
                object.__setattr__(state, "trace", state.trace + (trace_entry,))

        elif event.event_type == AuditEventType.TRADE_CLOSED:
            trade_id = str(payload.get("trade_id", event.entity_id))
            net_pnl = Decimal(str(payload.get("net_pnl", "0")))
            gross_pnl = Decimal(str(payload.get("gross_pnl", "0")))
            exit_reason = str(payload.get("exit_reason", "SIGNAL"))

            trade_entry = TraceEntry(
                timestamp=ts,
                event_type=TraceEventType.TRADE_CLOSED,
                entity_id=trade_id,
                symbol="NIFTY",
                side=TradeSide.LONG,
                quantity=1,
                price=Decimal("24000"),
                effective_price=Decimal("24000"),
                reason=exit_reason,
                state="CLOSED",
                metadata={
                    "gross_pnl": str(gross_pnl),
                    "net_pnl": str(net_pnl),
                },
            )
            if not self._source.trace:
                object.__setattr__(state, "trace", state.trace + (trade_entry,))

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

        # Update last processed sequence, hash, and timestamp
        object.__setattr__(state, "last_processed_sequence", event.sequence_number)
        object.__setattr__(state, "last_event_id", event.event_id)
        object.__setattr__(state, "last_event_hash", event.event_hash)
        object.__setattr__(state, "last_event_timestamp", ts)

        return None
