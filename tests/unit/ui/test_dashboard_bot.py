"""
Unit tests for AlphaForge Dashboard BOT Control System.

Guarantees tested:
1. BOT ON/OFF: Authoritative state machine (STOPPED -> STARTING -> RUNNING -> STOPPING -> ERROR).
2. PAPER / REAL Mode: PAPER is the only executable mode; REAL is rejected fail-closed.
3. Direct API requests to start REAL mode are rejected with 403 Forbidden.
4. Duplicate bot processes are prevented (409 Conflict / duplicate prevention).
5. Repeated stop calls are safe and idempotent.
6. Market closed condition is not treated as an engine failure.
7. Startup failures transition bot to ERROR with diagnostic messages.
8. Stale PID files are cleaned up safely.
9. Kill switch automatically halts the bot and blocks startup.
10. Full /api/bot/status and /api/status telemetry schemas.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from http.server import HTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.shadow_validation.upstox_adapter import UpstoxMarketDataAdapter
from scripts.dashboard_server import (
    STATE,
    AlphaForgeRequestHandler,
    BotManager,
    BotStatus,
    RealTradingForbiddenError,
    is_pid_active,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator
    from pathlib import Path


@pytest.fixture
def temp_bot_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Provide isolated paths for PID and stop-flag files."""
    pid_file = tmp_path / "paper_bot.pid"
    stop_flag = tmp_path / "bot_stop.flag"
    return pid_file, stop_flag


@pytest.fixture
def sleep_worker_cmd() -> list[str]:
    """Command that spawns a long-running benign python worker."""
    return [sys.executable, "-c", "import time; time.sleep(30)"]


# =====================================================================
# BotManager Unit Tests
# =====================================================================


def test_bot_starts_and_stops_gracefully(
    temp_bot_paths: tuple[Path, Path],
    sleep_worker_cmd: list[str],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    mgr = BotManager(command=sleep_worker_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    assert mgr.status.value == BotStatus.STOPPED.value
    assert mgr.pid is None

    # Start bot
    success, msg = mgr.start(mode="PAPER")
    try:
        assert success is True
        assert "started successfully" in msg
        assert mgr.status.value == BotStatus.RUNNING.value
        assert mgr.pid is not None
        assert is_pid_active(mgr.pid)
        assert pid_file.exists()
        assert int(pid_file.read_text(encoding="utf-8").strip()) == mgr.pid

        # Telemetry check while running
        telem = mgr.get_telemetry()
        assert telem["status"] == "RUNNING"
        assert telem["mode"] == "PAPER"
        assert telem["real_locked"] is True
        assert telem["real_trading_allowed"] is False
        assert telem["real_orders_count"] == 0
        assert telem["real_orders_status"] == "DISABLED"
        assert telem["pid"] == mgr.pid
    finally:
        # Graceful Stop
        stop_success, stop_msg = mgr.stop(timeout=5.0)
        assert stop_success is True
        assert "stopped gracefully" in stop_msg
        assert mgr.status.value == BotStatus.STOPPED.value
        assert mgr.pid is None
        assert not pid_file.exists()


def test_duplicate_start_does_not_create_duplicate_worker(
    temp_bot_paths: tuple[Path, Path],
    sleep_worker_cmd: list[str],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    mgr = BotManager(command=sleep_worker_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    success, _ = mgr.start(mode="PAPER")
    assert success is True
    initial_pid = mgr.pid
    assert initial_pid is not None

    try:
        # Second start attempt
        success2, msg2 = mgr.start(mode="PAPER")
        assert success2 is False
        assert "already" in msg2.lower()
        assert mgr.pid == initial_pid
    finally:
        mgr.stop()


def test_repeated_stop_is_safe_and_idempotent(
    temp_bot_paths: tuple[Path, Path],
    sleep_worker_cmd: list[str],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    mgr = BotManager(command=sleep_worker_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    # Stopping when already stopped
    success, msg = mgr.stop()
    assert success is True
    assert "already stopped" in msg

    # Start and stop, then stop again
    mgr.start(mode="PAPER")
    mgr.stop()
    assert mgr.status == BotStatus.STOPPED

    # Multiple successive stops
    success2, msg2 = mgr.stop()
    assert success2 is True
    assert "already stopped" in msg2

    success3, msg3 = mgr.stop()
    assert success3 is True


def test_real_mode_is_rejected_fail_closed(
    temp_bot_paths: tuple[Path, Path],
    sleep_worker_cmd: list[str],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    mgr = BotManager(command=sleep_worker_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    with pytest.raises(RealTradingForbiddenError) as exc_info:
        mgr.start(mode="REAL")

    assert "REAL_MODE_LOCKED" in str(exc_info.value)
    assert mgr.status == BotStatus.STOPPED
    assert mgr.pid is None


def test_startup_failure_enters_error_state(
    temp_bot_paths: tuple[Path, Path],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    # Non-zero exiting worker
    fail_cmd = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('CRITICAL_FEED_CRASH\\n'); sys.exit(2)",
    ]
    mgr = BotManager(command=fail_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    mgr.start(mode="PAPER")
    # Wait for process monitor thread to capture exit
    time.sleep(1.0)

    assert mgr.status == BotStatus.ERROR
    assert mgr.error_message is not None
    assert "CRITICAL_FEED_CRASH" in mgr.error_message or "2" in mgr.error_message


def test_stale_pid_cleanup(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "paper_bot.pid"
    stop_flag = tmp_path / "bot_stop.flag"

    # Write an inactive PID
    pid_file.write_text("9999999", encoding="utf-8")

    mgr = BotManager(command=["dummy"], pid_file=pid_file, stop_flag_file=stop_flag)
    # Recover stale PID should have cleaned up the stale file and set STOPPED
    assert mgr.status == BotStatus.STOPPED
    assert mgr.pid is None
    assert not pid_file.exists()


def test_market_closed_condition_not_treated_as_failure(
    temp_bot_paths: tuple[Path, Path],
    sleep_worker_cmd: list[str],
) -> None:
    pid_file, stop_flag = temp_bot_paths
    mgr = BotManager(command=sleep_worker_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    mgr.start(mode="PAPER")
    try:
        telem = mgr.get_telemetry()
        # Even if market is closed, bot status remains RUNNING
        assert telem["status"] == "RUNNING"
        assert telem["error_message"] is None
        assert "market_status" in telem
        assert isinstance(telem["is_market_open"], bool)
    finally:
        mgr.stop()


# =====================================================================
# Dashboard HTTP Server Integration Tests
# =====================================================================


@pytest.fixture(scope="module")
def bot_server_url() -> Generator[str, None, None]:
    server = HTTPServer(("127.0.0.1", 0), AlphaForgeRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _get_json(url: str) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body)


def _post_json(
    url: str,
    payload: dict[str, Any],
    csrf_token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    token = csrf_token if csrf_token is not None else STATE.csrf_token
    data_bytes = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-CSRF-Token": token}
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        parsed = json.loads(body) if body else {}
        return exc.code, parsed


def test_api_bot_status_endpoint(bot_server_url: str) -> None:
    status_code, data = _get_json(f"{bot_server_url}/api/bot/status")
    assert status_code == 200
    assert "status" in data
    assert data["mode"] == "PAPER"
    assert data["real_locked"] is True
    assert data["real_trading_allowed"] is False
    assert data["execution_mode"] == "PAPER_ONLY"
    assert data["market_data_mode"] == "LIVE_EXTERNAL_READ_ONLY"
    assert data["real_orders_count"] == 0
    assert data["real_orders_status"] == "DISABLED"


def test_api_status_rollup_includes_bot_telemetry(bot_server_url: str) -> None:
    status_code, data = _get_json(f"{bot_server_url}/api/status")
    assert status_code == 200
    assert "bot" in data
    assert data["bot"]["mode"] == "PAPER"
    assert data["bot"]["real_locked"] is True


def test_direct_api_attempt_to_start_real_is_rejected_403(bot_server_url: str) -> None:
    code, data = _post_json(f"{bot_server_url}/api/bot/start", {"mode": "REAL"})
    assert code == 403
    assert data["success"] is False
    assert "REAL_MODE_LOCKED" in data["error"]
    assert data["real_locked"] is True


def test_direct_api_start_paper_and_stop(bot_server_url: str, tmp_path: Path) -> None:
    # Configure STATE.bot_manager with a safe mock command for HTTP test
    pid_file = tmp_path / "api_test.pid"
    stop_flag = tmp_path / "api_test.flag"
    safe_cmd = [sys.executable, "-c", "import time; time.sleep(20)"]

    orig_mgr = STATE.bot_manager
    STATE.bot_manager = BotManager(command=safe_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    try:
        # Start in PAPER mode
        code, data = _post_json(f"{bot_server_url}/api/bot/start", {"mode": "PAPER"})
        assert code == 200
        assert data["success"] is True
        assert data["mode"] == "PAPER"
        assert data["real_locked"] is True
        assert data["status"] == "RUNNING"
        assert data["pid"] is not None

        # Duplicate start returns 409
        code2, data2 = _post_json(f"{bot_server_url}/api/bot/start", {"mode": "PAPER"})
        assert code2 == 409
        assert data2["success"] is False
        assert "already" in data2["error"].lower()

        # Stop bot
        code_stop, data_stop = _post_json(f"{bot_server_url}/api/bot/stop", {})
        assert code_stop == 200
        assert data_stop["success"] is True
        assert data_stop["status"] == "STOPPED"

        # Repeated stop is 200 OK
        code_stop2, data_stop2 = _post_json(f"{bot_server_url}/api/bot/stop", {})
        assert code_stop2 == 200
        assert data_stop2["success"] is True
    finally:
        STATE.bot_manager.stop()
        STATE.bot_manager = orig_mgr


def test_kill_switch_stops_running_bot_and_blocks_start(
    bot_server_url: str,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "ks_test.pid"
    stop_flag = tmp_path / "ks_test.flag"
    safe_cmd = [sys.executable, "-c", "import time; time.sleep(20)"]

    orig_mgr = STATE.bot_manager
    STATE.bot_manager = BotManager(command=safe_cmd, pid_file=pid_file, stop_flag_file=stop_flag)

    try:
        # Start bot
        _post_json(f"{bot_server_url}/api/bot/start", {"mode": "PAPER"})
        assert STATE.bot_manager.status.value == BotStatus.RUNNING.value

        # Engage kill switch
        code_ks, _ = _post_json(
            f"{bot_server_url}/api/kill-switch",
            {"action": "engage", "reason": "Test KS"},
        )
        assert code_ks == 200
        # Bot should have been stopped
        assert STATE.bot_manager.status.value == BotStatus.STOPPED.value

        # Attempt to start bot while KS is engaged
        code_start, data_start = _post_json(f"{bot_server_url}/api/bot/start", {"mode": "PAPER"})
        assert code_start == 403
        assert "Kill Switch is ENGAGED" in data_start["error"]
    finally:
        # Disarm kill switch and restore manager
        _post_json(
            f"{bot_server_url}/api/kill-switch",
            {"action": "disarm", "reason": "Test Reset"},
        )
        STATE.bot_manager.stop()
        STATE.bot_manager = orig_mgr


def test_bot_logging_to_runtime_files(tmp_path: Path) -> None:
    """Verifies stdout/stderr are redirected to files under log_dir."""
    pid_file = tmp_path / "paper_bot.pid"
    stop_flag = tmp_path / "bot_stop.flag"
    log_dir = tmp_path / "logs"

    # Worker that writes distinct stdout and stderr messages
    worker_cmd = [
        sys.executable,
        "-c",
        (
            "import sys, time; "
            "sys.stdout.write('DIAG_STDOUT_LINE_1\\n'); sys.stdout.flush(); "
            "sys.stderr.write('DIAG_STDERR_LINE_1\\n'); sys.stderr.flush(); "
            "time.sleep(30)"
        ),
    ]

    mgr = BotManager(
        command=worker_cmd,
        pid_file=pid_file,
        stop_flag_file=stop_flag,
        log_dir=log_dir,
    )

    success, _ = mgr.start(mode="PAPER")
    assert success is True
    assert mgr.status.value == BotStatus.RUNNING.value

    # Wait for process to flush writes
    time.sleep(1.0)

    try:
        assert mgr.stdout_log.exists()
        assert mgr.stderr_log.exists()

        stdout_content = mgr.stdout_log.read_text(encoding="utf-8")
        stderr_content = mgr.stderr_log.read_text(encoding="utf-8")

        assert "DIAG_STDOUT_LINE_1" in stdout_content
        assert "DIAG_STDERR_LINE_1" in stderr_content
    finally:
        mgr.stop()
        assert mgr.status.value == BotStatus.STOPPED.value


def test_graceful_stop_when_market_stream_has_no_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that when market stream emits zero ticks, stop wakes adapter promptly."""
    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        instrument_type=InstrumentType.FUTURES,
        expiry_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        listing_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_start_datetime=datetime(2026, 6, 1, 3, 45, tzinfo=UTC),
        trading_end_datetime=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        lot_size=50,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        price_decimal_places=2,
        currency="INR",
        settlement_type=SettlementType.CASH,
        status=ContractStatus.ACTIVE,
        data_source="NSE_MASTER",
    )

    adapter = UpstoxMarketDataAdapter(contract=contract, access_token="TEST_TOKEN")
    adapter._is_connected = True
    adapter._session_id = "TEST-SESSION-IDLE"

    # Simulate an empty live stream where zero market ticks are emitted
    async def empty_market_stream() -> AsyncIterator[Any]:
        while True:
            await asyncio.sleep(0.05)
            if False:
                yield None

    monkeypatch.setattr(adapter, "stream_live_events", empty_market_stream)

    stream_ended = threading.Event()

    def _consume() -> None:
        for _ in adapter.stream_events():
            pass
        stream_ended.set()

    consumer_thread = threading.Thread(target=_consume, daemon=True)
    consumer_thread.start()

    # Let consumer begin waiting on empty stream
    time.sleep(0.2)
    assert not stream_ended.is_set()

    # Trigger graceful disconnect
    t_start = time.monotonic()
    adapter.disconnect()

    # Must terminate promptly (<= 1.5 seconds) without hanging
    finished = stream_ended.wait(timeout=1.5)
    t_elapsed = time.monotonic() - t_start

    assert finished is True, "Stream failed to wake up and exit upon disconnect"
    assert t_elapsed <= 1.5
    consumer_thread.join(timeout=1.0)
    assert not consumer_thread.is_alive()

