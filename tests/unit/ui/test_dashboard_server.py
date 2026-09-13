"""
Unit tests for AlphaForge Operations Dashboard Server (scripts/dashboard_server.py).
Tests all REST endpoints, safety guards, reconciliation checks, forward batch runs,
order submissions, kill-switch behavior, and JSON/CSV data exports.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import HTTPServer
from typing import TYPE_CHECKING

import pytest

from scripts.dashboard_server import STATE, AlphaForgeRequestHandler

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(scope="module")
def server_url() -> Generator[str, None, None]:
    """Spins up a test HTTPServer on an ephemeral port."""
    server = HTTPServer(("127.0.0.1", 0), AlphaForgeRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict | str, dict]:
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        status = resp.status
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
        if "application/json" in content_type:
            return status, json.loads(body), dict(resp.headers)
        return status, body, dict(resp.headers)


def _post(url: str, payload: dict) -> tuple[int, dict, dict]:
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8")
            return status, json.loads(body), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}, dict(exc.headers)


def test_get_root_and_html(server_url: str) -> None:
    status, body, headers = _get(f"{server_url}/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert "AlphaForge Operations Console" in str(body)
    assert "PAPER MODE" in str(body)


def test_get_status(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/status")
    assert status == 200
    assert isinstance(data, dict)
    assert data["environment"] == "PAPER"
    assert data["real_broker_orders"] == 0
    assert data["commit_sha"] == "0060d39"
    assert "portfolio" in data
    assert "current_equity" in data["portfolio"]


def test_get_contract_metadata(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/contract")
    assert status == 200
    assert isinstance(data, dict)
    assert data["underlying_symbol"] == "NIFTY"
    assert data["contract_id"] == "NIFTY26SEPFUT"
    assert data["exchange"] == "NSE"
    assert data["lot_size"] == 50
    assert data["tradable"] is True


def test_get_risk_state(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/risk")
    assert status == 200
    assert isinstance(data, dict)
    assert data["risk_status"] in ("SAFE", "WARNING", "BLOCKED", "TRIGGERED")
    assert float(data["account_equity"]) >= 1000000.0


def test_get_safety_invariants(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/safety")
    assert status == 200
    assert isinstance(data, dict)
    checks = data["checks"]
    assert len(checks) == 10
    for c in checks:
        assert c["status"] == "PASS"


def test_get_continuous_reconciliation(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/reconciliation")
    assert status == 200
    assert isinstance(data, dict)
    assert data["all_aligned"] is True
    assert "intents" in data
    assert "fsm" in data
    assert "broker_orders" in data
    assert "broker_positions" in data
    assert "pnl_tracker" in data
    assert "audit_ledger" in data


def test_get_readiness_diagnostics(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/readiness?env=PAPER")
    assert status == 200
    assert isinstance(data, dict)
    assert data["is_ready"] is True

    # LIVE must fail-closed in simulated test
    status_live, data_live, _ = _get(f"{server_url}/api/readiness?env=LIVE")
    assert status_live == 200
    assert isinstance(data_live, dict)
    assert data_live["is_ready"] is False


def test_kill_switch_flow(server_url: str) -> None:
    # Engage
    status, data, _ = _post(
        f"{server_url}/api/kill-switch",
        {"action": "engage", "reason": "pytest"},
    )
    assert status == 200
    assert data["status"] == "ENGAGED"

    # Verify forward run blocked
    status_fwd, data_fwd, _ = _post(
        f"{server_url}/api/forward/run",
        {"mode": "PAPER", "candles_count": 20},
    )
    assert status_fwd == 403
    assert data_fwd["success"] is False

    # Disarm
    status_disarm, data_disarm, _ = _post(
        f"{server_url}/api/kill-switch",
        {"action": "disarm", "reason": "pytest"},
    )
    assert status_disarm == 200
    assert data_disarm["status"] == "DISARMED"


def test_forward_run_execution_and_export(server_url: str) -> None:
    # Execute batch run
    status, data, _ = _post(
        f"{server_url}/api/forward/run",
        {"mode": "PAPER", "candles_count": 50},
    )
    assert status == 200
    assert data["success"] is True
    run_id = data["run_id"]
    assert data["report"]["processed_candles"] == 50
    assert data["report"]["no_live_orders_submitted"] is True
    assert len(data["equity_curve"]) >= 1

    # Verify run in history
    hist_status, hist_data, _ = _get(f"{server_url}/api/forward/history")
    assert hist_status == 200
    assert isinstance(hist_data, list)
    assert any(h["run_id"] == run_id for h in hist_data)

    # Inspect run details
    detail_status, detail_data, _ = _get(f"{server_url}/api/forward/run/{run_id}")
    assert detail_status == 200
    assert detail_data["run_id"] == run_id

    # Export JSON
    exp_status, exp_body, exp_headers = _get(f"{server_url}/api/export/{run_id}?format=json")
    assert exp_status == 200
    assert "application/json" in exp_headers.get("Content-Type", "")
    assert isinstance(exp_body, dict)
    assert exp_body["run_id"] == run_id

    # Export CSV
    csv_status, csv_body, csv_headers = _get(f"{server_url}/api/export/{run_id}?format=csv")
    assert csv_status == 200
    assert "text/csv" in csv_headers.get("Content-Type", "")
    assert "Run ID,Trade ID,Timestamp" in str(csv_body)


def test_order_submission(server_url: str) -> None:
    # Disarm if engaged
    with STATE.lock:
        STATE.kill_switch.disarm(reason="pytest order submission test")

    status, data, _ = _post(
        f"{server_url}/api/orders/submit",
        {
            "symbol": "NIFTY26SEPFUT",
            "side": "BUY",
            "quantity": 50,
            "price": "25250.00",
            "role": "ENTRY",
        },
    )
    assert status == 200
    assert data["success"] is True
    assert "order_id" in data
    assert data["state"] == "ACKNOWLEDGED"
