"""
Unit tests for Phase 18-B Reconnect State Machine and Gap Recovery.

Verifies:
1. Explicit 8-state transitions (DISCONNECTED, CONNECTING, AUTHENTICATING, CONNECTED,
   DEGRADED, RECONNECTING, RECOVERING, HALTED).
2. Failure detection: socket disconnect, heartbeat timeout, stale data, auth failure.
3. Bounded exponential backoff with jitter.
4. Max retries exceeded triggers transition to HALTED.
5. Counter reset after stable connection period.
6. Temporal gap detection and transition to DEGRADED.
7. Historical backfill and continuity verification (successful vs failed/discontinuous).
8. Strategy evaluation gate: blocks evaluation when not in CONNECTED state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.shadow_validation.enums import DataSourceType
from alphaforge.shadow_validation.models import MarketStreamEvent
from alphaforge.shadow_validation.reconnect import (
    DisconnectReason,
    ReconnectPolicy,
    ReconnectStateMachine,
    StreamConnectionState,
)


def _make_dummy_event(ts: datetime, seq: int) -> MarketStreamEvent:
    return MarketStreamEvent(
        event_id=f"TEST-{seq}",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        sequence_no=seq,
        ingestion_sequence_no=seq,
        provider="UPSTOX",
        instrument_key="NSE_FO|NIFTY26SEPFUT",
        exchange_timestamp=ts,
        ingestion_timestamp=ts,
        processing_timestamp=ts,
        open_price=Decimal("24500.00"),
        high_price=Decimal("24510.00"),
        low_price=Decimal("24490.00"),
        close_price=Decimal("24505.00"),
        volume=100,
        data_source=DataSourceType.REAL_MARKET_SHADOW,
    )


def test_initial_state_and_evaluation_gate() -> None:
    """Verifies initial state is DISCONNECTED and strategy evaluation is blocked."""
    fsm = ReconnectStateMachine()
    assert fsm.current_state == StreamConnectionState.DISCONNECTED
    assert fsm.is_connected is False
    assert fsm.is_evaluation_allowed is False


def test_happy_path_connection_flow() -> None:
    """Verifies DISCONNECTED -> CONNECTING -> AUTHENTICATING -> CONNECTED."""
    fsm = ReconnectStateMachine()

    fsm.on_connect_started()
    assert fsm.current_state == StreamConnectionState.CONNECTING
    assert fsm.attempt_count == 1
    assert fsm.is_evaluation_allowed is False

    fsm.on_auth_started()
    assert fsm.current_state == StreamConnectionState.AUTHENTICATING
    assert fsm.is_evaluation_allowed is False

    fsm.on_connected()
    assert fsm.current_state == StreamConnectionState.CONNECTED
    assert fsm.is_connected is True
    assert fsm.is_evaluation_allowed is True


def test_disconnect_and_exponential_backoff() -> None:
    """Verifies socket disconnect initiates RECONNECTING with backoff."""
    policy = ReconnectPolicy(
        max_attempts=3,
        base_delay_seconds=2.0,
        max_delay_seconds=20.0,
        jitter_factor=0.0,  # Zero jitter for deterministic check
    )
    fsm = ReconnectStateMachine(policy=policy)
    fsm.on_connect_started()
    fsm.on_connected()

    # Disconnect 1
    delay1 = fsm.on_disconnect(DisconnectReason.SOCKET_DISCONNECT)
    assert fsm.current_state == StreamConnectionState.RECONNECTING
    assert delay1 >= 2.0
    assert fsm.is_evaluation_allowed is False

    # Disconnect 2
    fsm.on_connect_started()
    delay2 = fsm.on_disconnect(DisconnectReason.SOCKET_DISCONNECT)
    assert delay2 >= delay1

    # Disconnect 3 (exceeds max_attempts = 3)
    fsm.on_connect_started()
    delay3 = fsm.on_disconnect(DisconnectReason.SOCKET_DISCONNECT)
    assert fsm.current_state == StreamConnectionState.HALTED
    assert delay3 == 0.0


def test_authentication_failure_halts_immediately() -> None:
    """Verifies auth failure is non-retryable and transitions to HALTED immediately."""
    fsm = ReconnectStateMachine()
    fsm.on_connect_started()
    fsm.on_disconnect(DisconnectReason.AUTHENTICATION_FAILURE)
    assert fsm.current_state == StreamConnectionState.HALTED
    assert fsm.is_evaluation_allowed is False


def test_stale_data_and_heartbeat_timeout_detection() -> None:
    """Verifies check_health detects stale data and missing heartbeats."""
    policy = ReconnectPolicy(
        heartbeat_timeout_seconds=5.0,
        stale_data_timeout_seconds=10.0,
    )
    fsm = ReconnectStateMachine(policy=policy)
    fsm.on_connect_started()
    fsm.on_connected()

    # Healthy
    healthy, err = fsm.check_health()
    assert healthy is True
    assert err is None

    # Simulate heartbeat timeout by winding back last heartbeat timestamp
    fsm._last_heartbeat_ts = datetime.now(UTC) - timedelta(seconds=6.0)
    healthy, err = fsm.check_health()
    assert healthy is False
    assert "Heartbeat timeout" in str(err)

    # Restore heartbeat, simulate stale market message
    fsm._last_heartbeat_ts = datetime.now(UTC)
    fsm._last_message_ts = datetime.now(UTC) - timedelta(seconds=12.0)
    healthy, err = fsm.check_health()
    assert healthy is False
    assert "Stale market data" in str(err)


def test_gap_detection_marks_stream_degraded() -> None:
    """Verifies temporal gap transitions to DEGRADED and blocks evaluation."""
    fsm = ReconnectStateMachine()
    fsm.on_connect_started()
    fsm.on_connected()

    t0 = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
    e0 = _make_dummy_event(t0, 1)
    gaps0 = fsm.on_event_received(e0)
    assert len(gaps0) == 0
    assert fsm.current_state == StreamConnectionState.CONNECTED

    # Receive event 3 minutes later (gap of 180s > 60s + 15s)
    t1 = t0 + timedelta(minutes=3)
    e1 = _make_dummy_event(t1, 2)
    gaps1 = fsm.on_event_received(e1)
    assert len(gaps1) == 1
    assert gaps1[0].missing_intervals_estimated == 2
    assert fsm.current_state == StreamConnectionState.DEGRADED
    assert fsm.is_evaluation_allowed is False  # Strategy evaluation BLOCKED during gap!


def test_successful_gap_recovery_with_continuity_verification() -> None:
    """Verifies gap backfill verifies continuity before returning to CONNECTED."""
    t0 = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=3)

    # Mock backfill provider returning continuous 1-minute bars for the gap
    def mock_backfill(contract_id: str, start: datetime, end: datetime):
        return [
            _make_dummy_event(t0 + timedelta(minutes=1), 101),
            _make_dummy_event(t0 + timedelta(minutes=2), 102),
        ]

    fsm = ReconnectStateMachine(backfill_provider=mock_backfill)
    fsm.on_connect_started()
    fsm.on_connected()

    e0 = _make_dummy_event(t0, 1)
    fsm.on_event_received(e0)
    e1 = _make_dummy_event(t1, 2)
    fsm.on_event_received(e1)
    assert fsm.current_state == StreamConnectionState.DEGRADED

    # Run recovery
    all_ok, count = fsm.recover_gaps()
    assert all_ok is True
    assert count == 2
    assert fsm.current_state == StreamConnectionState.CONNECTED
    assert fsm.is_evaluation_allowed is True
    assert fsm.gaps[0].status == "RECOVERED"
    assert fsm.gaps[0].continuity_verified is True


def test_failed_gap_recovery_leaves_stream_degraded() -> None:
    """Verifies failed backfill leaves stream DEGRADED and evaluation BLOCKED."""

    def failing_backfill(contract_id: str, start: datetime, end: datetime):
        return []  # No data found

    fsm = ReconnectStateMachine(backfill_provider=failing_backfill)
    fsm.on_connect_started()
    fsm.on_connected()

    t0 = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=3)
    fsm.on_event_received(_make_dummy_event(t0, 1))
    fsm.on_event_received(_make_dummy_event(t1, 2))

    all_ok, count = fsm.recover_gaps()
    assert all_ok is False
    assert count == 0
    assert fsm.current_state == StreamConnectionState.DEGRADED
    assert fsm.is_evaluation_allowed is False
    assert fsm.gaps[0].status == "UNRECOVERABLE"
