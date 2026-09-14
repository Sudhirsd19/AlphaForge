"""
AlphaForge Market Data Reconnect & Gap Recovery State Machine (Phase 18-B).

Implements an explicit 8-state finite state machine for stream lifecycle management:
  DISCONNECTED -> CONNECTING -> AUTHENTICATING -> CONNECTED
                                                       |
                                                   DEGRADED
                                                       |
                                                 RECONNECTING
                                                       |
                                                  RECOVERING
                                                       |
                                                    HALTED

SAFETY INVARIANTS:
- No trading / strategy evaluation permitted during DEGRADED, RECONNECTING, RECOVERING,
  DISCONNECTED, or HALTED states (fail-closed evaluation gate).
- Never fabricate missing market data candles.
- Bounded exponential backoff with full jitter prevents reconnect storming.
- Consecutive failure threshold forces transition to HALTED state.
- Post-recovery continuity verification required before returning to CONNECTED.
- Full immutable audit record of all reconnect and recovery events.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from alphaforge.shadow_validation.models import MarketStreamEvent

logger = logging.getLogger(__name__)


class StreamConnectionState(str, Enum):
    """Explicit 8-state connection lifecycle."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    RECOVERING = "RECOVERING"
    HALTED = "HALTED"


class DisconnectReason(str, Enum):
    """Classified disconnection and degradation reasons."""

    CLEAN_SHUTDOWN = "CLEAN_SHUTDOWN"
    SOCKET_DISCONNECT = "SOCKET_DISCONNECT"
    HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    UNEXPECTED_TERMINATION = "UNEXPECTED_TERMINATION"
    GAP_DETECTED = "GAP_DETECTED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"


@dataclass(frozen=True)
class ReconnectEvent:
    """Immutable audit record for a state transition in the reconnect state machine."""

    timestamp: datetime
    from_state: StreamConnectionState
    to_state: StreamConnectionState
    reason: str
    attempt_number: int
    backoff_delay_seconds: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataGapRecord:
    """Record of a detected market data temporal gap."""

    gap_id: str
    symbol: str
    contract_id: str
    gap_start: datetime
    gap_end: datetime
    expected_interval_seconds: int
    missing_intervals_estimated: int
    detected_at: datetime
    status: str  # "DETECTED", "RECOVERING", "RECOVERED", "UNRECOVERABLE"
    recovered_events_count: int = 0
    continuity_verified: bool = False


class ReconnectPolicy:
    """
    Configuration and calculation for bounded exponential backoff with jitter.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
        jitter_factor: float = 0.5,
        stable_reset_seconds: float = 60.0,
        heartbeat_timeout_seconds: float = 10.0,
        stale_data_timeout_seconds: float = 15.0,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.jitter_factor = jitter_factor
        self.stable_reset_seconds = stable_reset_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.stale_data_timeout_seconds = stale_data_timeout_seconds

    def compute_backoff(self, attempt: int) -> float:
        """
        Compute exponential backoff with full jitter:
        delay = min(max_delay, base_delay * 2 ** (attempt - 1)) + jitter
        """
        if attempt <= 0:
            return 0.0
        exp_delay = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        jitter = random.uniform(-self.jitter_factor * exp_delay, self.jitter_factor * exp_delay)
        return float(max(self.base_delay_seconds, min(self.max_delay_seconds, exp_delay + jitter)))


class ReconnectStateMachine:
    """
    State machine governing streaming market data connection, health monitoring,
    gap detection, and recovery.
    """

    def __init__(
        self,
        policy: ReconnectPolicy | None = None,
        backfill_provider: Callable[[str, datetime, datetime], list[MarketStreamEvent]]
        | None = None,
    ) -> None:
        self._policy = policy or ReconnectPolicy()
        self._backfill_provider = backfill_provider

        # Lifecycle
        self._current_state = StreamConnectionState.DISCONNECTED
        self._state_entered_ts = datetime.now(UTC)
        self._events_log: list[ReconnectEvent] = []
        self._gap_records: list[DataGapRecord] = []

        # Attempt tracking
        self._attempt_count: int = 0
        self._last_successful_connection_ts: datetime | None = None
        self._last_heartbeat_ts: datetime | None = None
        self._last_message_ts: datetime | None = None
        self._last_event_ts: datetime | None = None

        # Degraded tracking
        self._degraded_reason: str | None = None

    @property
    def current_state(self) -> StreamConnectionState:
        return self._current_state

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    @property
    def is_connected(self) -> bool:
        return self._current_state == StreamConnectionState.CONNECTED

    @property
    def is_evaluation_allowed(self) -> bool:
        """
        FAIL-CLOSED STRATEGY GATE:
        Returns True ONLY when in CONNECTED state.
        Evaluation is strictly forbidden during DEGRADED, RECONNECTING,
        RECOVERING, DISCONNECTED, or HALTED states.
        """
        return self._current_state == StreamConnectionState.CONNECTED

    @property
    def audit_events(self) -> list[ReconnectEvent]:
        return list(self._events_log)

    @property
    def gaps(self) -> list[DataGapRecord]:
        return list(self._gap_records)

    def transition_to(
        self,
        new_state: StreamConnectionState,
        reason: str,
        delay_seconds: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Execute and record state transition."""
        prev_state = self._current_state
        now = datetime.now(UTC)

        event = ReconnectEvent(
            timestamp=now,
            from_state=prev_state,
            to_state=new_state,
            reason=reason,
            attempt_number=self._attempt_count,
            backoff_delay_seconds=delay_seconds,
            details=details or {},
        )
        self._events_log.append(event)
        self._current_state = new_state
        self._state_entered_ts = now

        logger.info(
            "StreamConnectionState transition: %s -> %s [reason: %s, attempt: %d]",
            prev_state.value,
            new_state.value,
            reason,
            self._attempt_count,
        )

    # --- State Trigger Methods ---

    def on_connect_started(self) -> None:
        """Initiate connection sequence."""
        self._attempt_count += 1
        self.transition_to(StreamConnectionState.CONNECTING, "Initiating connection sequence")

    def on_auth_started(self) -> None:
        """Connection socket established; initiating auth handshake."""
        self.transition_to(
            StreamConnectionState.AUTHENTICATING, "WebSocket open; authenticating token"
        )

    def on_connected(self) -> None:
        """Successfully established stream and subscribed."""
        now = datetime.now(UTC)
        self._last_successful_connection_ts = now
        self._last_heartbeat_ts = now
        self._last_message_ts = now
        self._degraded_reason = None
        self.transition_to(StreamConnectionState.CONNECTED, "Stream connected and operational")

    def on_heartbeat(self) -> None:
        """Record provider heartbeat."""
        now = datetime.now(UTC)
        self._last_heartbeat_ts = now
        self._last_message_ts = now
        self._check_counter_reset(now)

    def on_message_received(self) -> None:
        """Record provider message receipt."""
        now = datetime.now(UTC)
        self._last_message_ts = now
        self._check_counter_reset(now)

    def on_event_received(self, event: MarketStreamEvent) -> list[DataGapRecord]:
        """
        Record verified market stream event and check for temporal gaps against
        the prior event timestamp.
        """
        now = datetime.now(UTC)
        self._last_message_ts = now
        self._check_counter_reset(now)

        new_gaps: list[DataGapRecord] = []
        if self._last_event_ts is not None:
            gap = self._evaluate_gap(self._last_event_ts, event.exchange_timestamp, event)
            if gap:
                new_gaps.append(gap)
                self._gap_records.append(gap)
                self.transition_to(
                    StreamConnectionState.DEGRADED,
                    DisconnectReason.GAP_DETECTED.value,
                    details={
                        "gap_id": gap.gap_id,
                        "start": gap.gap_start.isoformat(),
                        "end": gap.gap_end.isoformat(),
                    },
                )

        self._last_event_ts = event.exchange_timestamp
        return new_gaps

    def on_disconnect(
        self, reason: DisconnectReason, details: dict[str, Any] | None = None
    ) -> float:
        """
        Handle disconnection event.
        Calculates backoff delay and transitions to RECONNECTING or HALTED.
        Returns the computed backoff delay in seconds.
        """
        if reason == DisconnectReason.CLEAN_SHUTDOWN:
            self.transition_to(StreamConnectionState.DISCONNECTED, reason.value, details=details)
            return 0.0

        if reason == DisconnectReason.AUTHENTICATION_FAILURE:
            self.transition_to(
                StreamConnectionState.HALTED,
                "Authentication failure is non-retryable with current credentials",
                details=details,
            )
            return 0.0

        if self._attempt_count >= self._policy.max_attempts:
            self.transition_to(
                StreamConnectionState.HALTED,
                f"Exceeded maximum reconnect attempts ({self._policy.max_attempts})",
                details=details,
            )
            return 0.0

        delay = self._policy.compute_backoff(self._attempt_count + 1)
        self.transition_to(
            StreamConnectionState.RECONNECTING,
            reason.value,
            delay_seconds=delay,
            details=details,
        )
        return delay

    def check_health(self) -> tuple[bool, str | None]:
        """
        Periodic health audit. Checks:
        1. Heartbeat timeout
        2. Stale market data
        Returns (is_healthy, failure_reason_if_any)
        """
        if self._current_state != StreamConnectionState.CONNECTED:
            return False, f"Not connected (current state: {self._current_state.value})"

        now = datetime.now(UTC)

        # Check heartbeat
        if self._last_heartbeat_ts:
            hb_age = (now - self._last_heartbeat_ts).total_seconds()
            if hb_age > self._policy.heartbeat_timeout_seconds:
                return (
                    False,
                    f"Heartbeat timeout: {hb_age:.1f}s > {self._policy.heartbeat_timeout_seconds}s",
                )

        # Check stale data
        if self._last_message_ts:
            msg_age = (now - self._last_message_ts).total_seconds()
            if msg_age > self._policy.stale_data_timeout_seconds:
                return (
                    False,
                    f"Stale market data: {msg_age:.1f}s > {self._policy.stale_data_timeout_seconds}s",
                )

        return True, None

    # --- Gap Recovery & Continuity ---

    def recover_gaps(self) -> tuple[bool, int]:
        """
        Attempt backfill for all unresolved gaps using the backfill provider.
        Returns (all_gaps_recovered_and_continuous, total_recovered_count).
        """
        if not self._gap_records:
            return True, 0

        if not self._backfill_provider:
            logger.warning("No backfill provider registered. Gaps remain unrecovered.")
            self.transition_to(
                StreamConnectionState.DEGRADED,
                "Unrecoverable gaps: no backfill provider available",
            )
            return False, 0

        self.transition_to(StreamConnectionState.RECOVERING, "Starting historical gap backfill")

        total_recovered = 0
        all_continuous = True

        updated_records: list[DataGapRecord] = []
        for gap in self._gap_records:
            if gap.status == "RECOVERED":
                updated_records.append(gap)
                continue

            try:
                recovered_events = self._backfill_provider(
                    gap.contract_id, gap.gap_start, gap.gap_end
                )
                if not recovered_events:
                    updated_records.append(
                        DataGapRecord(
                            gap_id=gap.gap_id,
                            symbol=gap.symbol,
                            contract_id=gap.contract_id,
                            gap_start=gap.gap_start,
                            gap_end=gap.gap_end,
                            expected_interval_seconds=gap.expected_interval_seconds,
                            missing_intervals_estimated=gap.missing_intervals_estimated,
                            detected_at=gap.detected_at,
                            status="UNRECOVERABLE",
                            recovered_events_count=0,
                            continuity_verified=False,
                        )
                    )
                    all_continuous = False
                    continue

                is_continuous = self._verify_continuity(
                    gap.gap_start, gap.gap_end, recovered_events, gap.expected_interval_seconds
                )
                total_recovered += len(recovered_events)

                updated_records.append(
                    DataGapRecord(
                        gap_id=gap.gap_id,
                        symbol=gap.symbol,
                        contract_id=gap.contract_id,
                        gap_start=gap.gap_start,
                        gap_end=gap.gap_end,
                        expected_interval_seconds=gap.expected_interval_seconds,
                        missing_intervals_estimated=gap.missing_intervals_estimated,
                        detected_at=gap.detected_at,
                        status="RECOVERED" if is_continuous else "PARTIAL",
                        recovered_events_count=len(recovered_events),
                        continuity_verified=is_continuous,
                    )
                )
                if not is_continuous:
                    all_continuous = False

            except Exception as exc:
                logger.error("Gap recovery failed for %s — %s", gap.gap_id, exc)
                updated_records.append(
                    DataGapRecord(
                        gap_id=gap.gap_id,
                        symbol=gap.symbol,
                        contract_id=gap.contract_id,
                        gap_start=gap.gap_start,
                        gap_end=gap.gap_end,
                        expected_interval_seconds=gap.expected_interval_seconds,
                        missing_intervals_estimated=gap.missing_intervals_estimated,
                        detected_at=gap.detected_at,
                        status="RECOVERY_FAILED",
                        recovered_events_count=0,
                        continuity_verified=False,
                    )
                )
                all_continuous = False

        self._gap_records = updated_records

        if all_continuous:
            self.transition_to(
                StreamConnectionState.CONNECTED,
                f"All gaps recovered and continuity verified ({total_recovered} events)",
            )
        else:
            self.transition_to(
                StreamConnectionState.DEGRADED,
                "Gap recovery completed with gaps or discontinuities remaining",
            )

        return all_continuous, total_recovered

    # --- Internal Helpers ---

    def _evaluate_gap(
        self, prev_ts: datetime, curr_ts: datetime, event: MarketStreamEvent
    ) -> DataGapRecord | None:
        """
        Check if delta between consecutive closed candles exceeds standard 1m interval (60s).
        """
        delta_seconds = (curr_ts - prev_ts).total_seconds()
        expected_seconds = 60
        tolerance_seconds = 15

        if delta_seconds > (expected_seconds + tolerance_seconds):
            missing_estimate = max(1, int(round(delta_seconds / expected_seconds)) - 1)
            gap_id = f"GAP-{event.contract_id}-{int(prev_ts.timestamp())}-{int(curr_ts.timestamp())}"
            return DataGapRecord(
                gap_id=gap_id,
                symbol=event.symbol,
                contract_id=event.contract_id,
                gap_start=prev_ts + timedelta(seconds=expected_seconds),
                gap_end=curr_ts - timedelta(seconds=expected_seconds),
                expected_interval_seconds=expected_seconds,
                missing_intervals_estimated=missing_estimate,
                detected_at=datetime.now(UTC),
                status="DETECTED",
            )
        return None

    def _verify_continuity(
        self,
        start_ts: datetime,
        end_ts: datetime,
        events: list[MarketStreamEvent],
        expected_interval_seconds: int,
    ) -> bool:
        """Verify that backfilled events form an unbroken sequence covering the gap range."""
        if not events:
            return False

        sorted_events = sorted(events, key=lambda e: e.exchange_timestamp)

        # Check start coverage
        if (
            sorted_events[0].exchange_timestamp - start_ts
        ).total_seconds() > expected_interval_seconds:
            return False

        # Check internal step continuity
        for i in range(1, len(sorted_events)):
            step = (
                sorted_events[i].exchange_timestamp - sorted_events[i - 1].exchange_timestamp
            ).total_seconds()
            if step > (expected_interval_seconds + 15):
                return False

        # Check end coverage
        if (end_ts - sorted_events[-1].exchange_timestamp).total_seconds() > expected_interval_seconds:
            return False

        return True

    def _check_counter_reset(self, now: datetime) -> None:
        """Reset reconnect attempt counter after sustained stability."""
        if (
            self._attempt_count > 0
            and self._last_successful_connection_ts is not None
            and (now - self._last_successful_connection_ts).total_seconds()
            >= self._policy.stable_reset_seconds
        ):
            logger.info(
                "Resetting reconnect attempt counter (%d -> 0) after %.1fs stable streaming",
                self._attempt_count,
                self._policy.stable_reset_seconds,
            )
            self._attempt_count = 0
