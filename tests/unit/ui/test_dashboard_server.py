"""
Unit tests for AlphaForge Quantitative Operations Console Server (scripts/dashboard_server.py).
Tests Phase 17 architecture:
- Runtime Git SHA resolution
- Dynamic ContractMaster metadata & lifecycle
- Phase 17C shadow status, health, provenance, market data, and preflight
- Signal engine state and decision explainer
- Discovered evidence packages
- Synthetic forward batch run separation
- Paper order simulator isolation (zero live orders)
- Safety guards, kill switch, and reconciliation
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from http.server import HTTPServer
from typing import TYPE_CHECKING

import pytest

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.contract.models import ContractMaster
from alphaforge.contract.repository import InMemoryContractMasterRepository
from alphaforge.data.enums import InstrumentType
from alphaforge.shadow_validation.contract_source import AuthoritativeContractSource
from alphaforge.shadow_validation.upstox_adapter import UpstoxMarketDataAdapter
from scripts.dashboard_server import STATE, AlphaForgeRequestHandler, get_runtime_git_sha

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


def _post(
    url: str,
    payload: dict,
    csrf_token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, dict]:
    token = csrf_token if csrf_token is not None else STATE.csrf_token
    data_bytes = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if token:
        req_headers["X-CSRF-Token"] = token
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data_bytes,
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8")
            return status, json.loads(body), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed_json = json.loads(body) if body else {}
            return exc.code, parsed_json, dict(exc.headers)
        except Exception:
            return exc.code, body, dict(exc.headers)


# --- Basic & Core Endpoints ---


def test_get_root_and_html(server_url: str) -> None:
    status, body, headers = _get(f"{server_url}/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert "AlphaForge Operations Console" in str(body)
    assert "REAL-MARKET SHADOW" in str(body)
    assert "ZERO LIVE ORDERS" in str(body)


def test_runtime_git_sha(server_url: str) -> None:
    """Verifies that Git SHA is derived from runtime repository, not hard-coded."""
    status, data, _ = _get(f"{server_url}/api/status")
    assert status == 200
    assert isinstance(data, dict)
    expected_sha = get_runtime_git_sha()
    assert data["commit_sha"] == expected_sha
    assert len(data["commit_sha"]) >= 7
    assert data["commit_sha"] != "0060d39"  # Must NOT be the old Phase 16 baseline SHA


def test_get_status(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/status")
    assert status == 200
    assert isinstance(data, dict)
    assert data["environment"] == "PAPER"
    assert data["mode"] == "REAL-MARKET SHADOW"
    assert data["data_provider"] == "UPSTOX"
    assert data["real_broker_orders"] == 0
    assert data["real_routing_blocked"] is True
    assert "portfolio" in data
    assert "current_equity" in data["portfolio"]
    assert "session_status" in data


def test_dynamic_contract(server_url: str) -> None:
    """Verifies contract metadata is bound dynamically to authoritative ContractMaster."""
    status, data, _ = _get(f"{server_url}/api/contract")
    assert status == 200
    assert isinstance(data, dict)
    assert data["underlying"] == "NIFTY"
    assert data["exchange"] == "NSE"
    assert data["segment"] == "NFO"
    assert data["instrument_type"] == "FUTURES"
    assert data["lot_size"] == 50
    assert data["tradable"] is True
    assert "expiry" in data
    assert "lifecycle" in data


# --- Phase 17 Shadow Endpoints ---


def test_get_shadow_status(server_url: str) -> None:
    """Verifies /api/shadow/status reports read-only Upstox configuration."""
    status, data, _ = _get(f"{server_url}/api/shadow/status")
    assert status == 200
    assert isinstance(data, dict)
    assert data["provider"] == "UPSTOX"
    assert data["mode"] == "REAL-MARKET SHADOW"
    assert data["connection_status"] in ("DISCONNECTED", "CONNECTED", "CONNECTING")
    assert data["live_orders"] == 0
    assert data["live_broker_calls"] == 0
    assert data["zero_live_orders_assured"] is True
    assert data["phase17c_status"] in (
        "READY",
        "READY FOR REAL-MARKET RUN",
        "BLOCKED",
        "RUNNING",
        "PENDING",
        "PASS",
    )


def test_get_shadow_health(server_url: str) -> None:
    """Verifies /api/shadow/health exposes honest telemetry counters without fabrication."""
    status, data, _ = _get(f"{server_url}/api/shadow/health")
    assert status == 200
    assert isinstance(data, dict)
    assert data["provider"] == "UPSTOX"
    assert data["health_level"] in ("DISCONNECTED", "HEALTHY", "WARNING", "DEGRADED")
    assert data["messages_received"] >= 0
    assert data["valid_events"] >= 0
    assert data["invalid_events"] >= 0
    assert data["duplicates"] >= 0
    assert data["sequence_gaps"] >= 0


def test_get_shadow_provenance(server_url: str) -> None:
    """Verifies provenance endpoint uses INTERNAL ALPHAFORGE PROVENANCE ATTESTATION label."""
    status, data, _ = _get(f"{server_url}/api/shadow/provenance")
    assert status == 200
    assert isinstance(data, dict)
    assert data["provider"] == "UPSTOX"
    assert data["attestation_type"] == "INTERNAL ALPHAFORGE PROVENANCE ATTESTATION"
    # Must NOT call internal HMAC an Upstox or provider signature
    assert "UPSTOX SIGNATURE" not in str(data)
    assert "PROVIDER SIGNATURE" not in str(data)


def test_get_shadow_market(server_url: str) -> None:
    """Verifies /api/shadow/market returns honest empty state when no stream is active."""
    status, data, _ = _get(f"{server_url}/api/shadow/market")
    assert status == 200
    assert isinstance(data, dict)
    assert data["provider"] == "UPSTOX"
    if not data["active"]:
        assert data["message"] == "NO REAL-MARKET SESSION ACTIVE"
        assert data["candles"] == []


def test_get_signals_state(server_url: str) -> None:
    """Verifies /api/signals/state returns strategy decision state and explainer history."""
    status, data, _ = _get(f"{server_url}/api/signals/state")
    assert status == 200
    assert isinstance(data, dict)
    assert "strategy_state" in data
    assert "trend" in data
    assert "momentum" in data
    assert "risk_gate" in data
    assert "last_decision" in data
    assert isinstance(data["decision_history"], list)
    for d in data["decision_history"]:
        assert "category" in d
        assert "reason" in d


def test_get_shadow_evidence(server_url: str) -> None:
    """Verifies /api/shadow/evidence discovers packages from evidence directory."""
    status, data, _ = _get(f"{server_url}/api/shadow/evidence")
    assert status == 200
    assert isinstance(data, list)


def test_shadow_preflight(server_url: str) -> None:
    """Verifies /api/shadow/preflight validates credentials, guards, and contract."""
    status, data, _ = _post(f"{server_url}/api/shadow/preflight", {})
    assert status == 200
    assert isinstance(data, dict)
    assert data["provider"] == "UPSTOX"
    assert data["live_orders"] == 0
    assert data["live_broker_calls"] == 0
    assert "guard_status" in data
    assert "ready" in data


# --- Synthetic / Paper Separation & Safety ---


def test_synthetic_forward_is_labeled(server_url: str) -> None:
    """Verifies forward batch runs are explicitly marked as SYNTHETIC / PAPER."""
    with STATE.lock:
        STATE.kill_switch.disarm(reason="pytest forward run")

    status, data, _ = _post(
        f"{server_url}/api/forward/run",
        {"mode": "PAPER", "candles_count": 20},
    )
    assert status == 200
    assert data["success"] is True
    assert data["data_source"] == "SYNTHETIC"
    assert data["execution_mode"] == "PAPER"
    # Must NOT report REAL_MARKET_SHADOW
    assert data["data_source"] != "REAL_MARKET_SHADOW"


def test_backtest_latest_endpoint(server_url: str) -> None:
    """Verifies GET /api/backtest/latest returns latest report if present."""
    status, data, _ = _get(f"{server_url}/api/backtest/latest")
    assert status == 200
    assert "success" in data
    if data["success"]:
        assert "data" in data
        assert data["data"]["strategy_id"] == "AF_ORB_MOMENTUM_V1"


def test_real_forward_run_endpoint(server_url: str) -> None:
    """Verifies POST /api/forward/run with use_real=True executes real backtest or falls back."""
    with STATE.lock:
        STATE.kill_switch.disarm(reason="pytest real forward run")

    status, data, _ = _post(
        f"{server_url}/api/forward/run",
        {"mode": "PAPER", "candles_count": 150, "use_real": True},
    )
    assert status == 200
    assert data["success"] is True
    assert "report" in data
    assert "trades" in data


def test_paper_order_simulator(server_url: str) -> None:
    """Verifies order submission is strictly a PaperBroker simulator."""
    with STATE.lock:
        STATE.kill_switch.disarm(reason="pytest order simulator")

    status, data, _ = _post(
        f"{server_url}/api/orders/submit",
        {
            "side": "BUY",
            "quantity": 50,
            "price": "25250.00",
            "role": "ENTRY",
        },
    )
    assert status == 200
    assert data["success"] is True
    assert data["simulation"] is True
    assert data["order_id"].startswith("SIM-ORD-")
    assert "Simulation Only - Zero Live Orders" in data["message"]


def test_no_live_order_api(server_url: str) -> None:
    """Verifies no live broker order placement endpoints exist."""
    # Attempting to POST to live broker endpoints should 404
    status, _, _ = _post(f"{server_url}/api/v1/orders/live", {})
    assert status == 404
    status_upstox, _, _ = _post(f"{server_url}/api/upstox/order/place", {})
    assert status_upstox == 404


def test_no_fake_live_status(server_url: str) -> None:
    """Verifies connection status is not falsely reported as CONNECTED without socket."""
    status, data, _ = _get(f"{server_url}/api/shadow/status")
    assert status == 200
    if not STATE.shadow_session_active:
        assert data["connection_status"] == "DISCONNECTED"
        assert data["external_live_feed"] is False


def test_no_fake_provenance(server_url: str) -> None:
    """Verifies provenance is not claimed as verified when no session is active."""
    status, data, _ = _get(f"{server_url}/api/shadow/provenance")
    assert status == 200
    if not STATE.shadow_session_active:
        assert data["provenance_verified"] != "VERIFIED"


def test_csrf_endpoint_and_protection(server_url: str) -> None:
    """Verifies CSRF endpoint returns token and POST fails closed without token."""
    # 1. GET CSRF token
    status, data, _ = _get(f"{server_url}/api/security/csrf")
    assert status == 200
    assert "csrf_token" in data
    assert len(data["csrf_token"]) >= 32

    # 2. POST without CSRF token is rejected with 403
    status_no_token, data_no_token, _ = _post(
        f"{server_url}/api/kill-switch",
        {"action": "engage", "reason": "pytest no token"},
        csrf_token="",
    )
    assert status_no_token == 403
    assert "CSRF_FORBIDDEN" in data_no_token["error"]

    # 3. POST with invalid CSRF token is rejected with 403
    status_bad_token, data_bad_token, _ = _post(
        f"{server_url}/api/kill-switch",
        {"action": "engage", "reason": "pytest bad token"},
        csrf_token="bad-csrf-token-123",
    )
    assert status_bad_token == 403
    assert "CSRF_FORBIDDEN" in data_bad_token["error"]


def test_cors_origin_restriction(server_url: str) -> None:
    """Verifies CORS headers are restricted to localhost/127.0.0.1 and not wildcard *."""
    req_local = urllib.request.Request(  # noqa: S310
        f"{server_url}/api/status",
        headers={"Origin": "http://localhost:8080"},
        method="GET",
    )
    with urllib.request.urlopen(req_local) as resp:  # noqa: S310
        assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    req_external = urllib.request.Request(  # noqa: S310
        f"{server_url}/api/status",
        headers={"Origin": "https://malicious-site.com"},
        method="GET",
    )
    with urllib.request.urlopen(req_external) as resp:  # noqa: S310
        assert resp.headers.get("Access-Control-Allow-Origin") is None


def test_secret_redaction(server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies secrets like UPSTOX_ACCESS_TOKEN are scrubbed from responses and logs."""
    fake_token = "SECRET_SUPER_CONFIDENTIAL_TOKEN_999"
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", fake_token)

    STATE.log_event("TEST", "SECURITY", f"Connecting using {fake_token} now")
    status, data, _ = _get(f"{server_url}/api/status")
    assert status == 200
    serialized = json.dumps(data)
    assert fake_token not in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_truthful_provenance_and_market_defaults(server_url: str) -> None:
    """Verifies provenance and market endpoints return truthful null/NOT AVAILABLE when inactive."""
    status_prov, prov_data, _ = _get(f"{server_url}/api/shadow/provenance")
    assert status_prov == 200
    if not STATE.shadow_session_active:
        assert prov_data["raw_payload_hash"] == "NOT AVAILABLE"
        assert prov_data["alpha_forge_attestation_hmac"] == "NOT AVAILABLE"
        assert "SHA256:WIRE-PAYLOAD" not in str(prov_data)
        assert "HMAC256:INTERNAL-ATTESTATION" not in str(prov_data)

    status_mkt, mkt_data, _ = _get(f"{server_url}/api/shadow/market")
    assert status_mkt == 200
    assert mkt_data["overlays"] is None


# --- Pre-existing Tests Preserved ---


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


def test_get_readiness_diagnostics(server_url: str) -> None:
    status, data, _ = _get(f"{server_url}/api/readiness?env=PAPER")
    assert status == 200
    assert isinstance(data, dict)
    assert data["is_ready"] is True

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


# --- Authoritative Contract Source Tests ---


def test_contract_source_is_authoritative() -> None:
    src = AuthoritativeContractSource()
    c = src.get_active_contract()
    assert c is not None
    assert c.exchange == "NSE"
    assert c.segment in ("NFO", "NSE_FO")
    assert c.underlying_symbol == "NIFTY"
    assert c.instrument_type == InstrumentType.FUTURES
    assert c.lot_size == 50
    assert c.tick_size == Decimal("0.05")
    assert c.data_source == "NSE_MASTER"
    assert not c.data_source.upper().startswith(("SYNTHETIC", "TEST"))

    state = src.resolve_contract_state()
    assert state["status"] in ("ACTIVE", "EXPIRING")
    assert state["tradable"] is True
    assert state["exchange"] == "NSE"
    assert state["segment"] == "NFO"
    assert state["underlying"] == "NIFTY"
    assert state["is_synthetic"] is False


def test_stale_contract_not_presented_as_active() -> None:
    src = AuthoritativeContractSource()
    # Before listing datetime in 2025
    prior_ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    c_prior = src.get_active_contract(prior_ts)
    assert c_prior is None
    state_prior = src.resolve_contract_state(prior_ts)
    assert state_prior["status"] != "ACTIVE"
    assert state_prior["tradable"] is False


def test_expired_contract_is_not_tradable() -> None:
    src = AuthoritativeContractSource()
    # Future timestamp in 2027 where all 2026 contracts are expired
    future_ts = datetime(2027, 6, 1, 10, 0, tzinfo=UTC)
    c = src.get_active_contract(future_ts)
    assert c is None
    state = src.resolve_contract_state(future_ts)
    assert state["status"] == "EXPIRED"
    assert state["tradable"] is False


def test_rollover_updates_active_contract() -> None:
    src = AuthoritativeContractSource()
    # 1. Front-month contract is NIFTY26SEPFUT at 2026-09-14
    sep_ts = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    c_sep = src.get_active_contract(sep_ts)
    assert c_sep is not None
    assert c_sep.contract_id == "NIFTY26SEPFUT"

    # 2. 1 hour before Sep expiry (2026-09-24 09:00 UTC) -> Rollover candidate detected
    expiring_ts = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
    rollover = src.get_rollover_candidate(expiring_ts)
    assert rollover is not None
    front, next_c = rollover
    assert front.contract_id == "NIFTY26SEPFUT"
    assert next_c.contract_id == "NIFTY26OCTFUT"

    # 3. After Sep expiry (2026-09-24 10:05 UTC) -> Rolls over to NIFTY26OCTFUT
    post_sep_ts = datetime(2026, 9, 24, 10, 5, tzinfo=UTC)
    c_oct = src.get_active_contract(post_sep_ts)
    assert c_oct is not None
    assert c_oct.contract_id == "NIFTY26OCTFUT"
    assert c_oct.expiry_datetime == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)

    state_oct = src.resolve_contract_state(post_sep_ts)
    assert state_oct["contract_id"] == "NIFTY26OCTFUT"
    assert state_oct["status"] == "ACTIVE"
    assert state_oct["tradable"] is True


def test_upstox_contract_matches_dashboard_contract(server_url: str) -> None:
    # Query dashboard endpoint
    status, data, _ = _get(f"{server_url}/api/contract")
    assert status == 200
    dash_contract_id = data["contract_id"]

    # Verify authoritative contract source resolves the same contract
    src = AuthoritativeContractSource()
    active_contract = src.get_active_contract()
    assert active_contract is not None
    assert active_contract.contract_id == dash_contract_id

    # Verify UpstoxMarketDataAdapter initializes with matching contract and instrument key
    adapter = UpstoxMarketDataAdapter(contract=active_contract, access_token="DUMMY_TOKEN")
    assert adapter.contract.contract_id == dash_contract_id
    assert adapter._instrument_key == f"NSE_FO|{dash_contract_id}"


def test_contract_unavailable_fails_closed() -> None:
    # Empty repository
    empty_repo = InMemoryContractMasterRepository()
    src = AuthoritativeContractSource(repository=empty_repo)
    empty_repo.clear()
    c = src.get_active_contract()
    assert c is None
    state = src.resolve_contract_state()
    assert state["status"] == "UNAVAILABLE"
    assert state["tradable"] is False
    assert state["contract_id"] == "NONE"


def test_synthetic_contract_not_presented_as_real() -> None:
    synth_contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY-SYNTH-FUT",
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
        data_source="SYNTHETIC_TEST",
    )
    repo = InMemoryContractMasterRepository()
    repo.add_contract(synth_contract)

    # Real-market mode (allow_synthetic=False) must reject synthetic contract
    real_src = AuthoritativeContractSource(repository=repo, allow_synthetic=False)
    active = real_src.get_active_contract(datetime(2026, 9, 14, 10, 0, tzinfo=UTC))
    assert active is None
    state = real_src.resolve_contract_state(datetime(2026, 9, 14, 10, 0, tzinfo=UTC))
    assert state["status"] == "UNAVAILABLE"
    assert state["tradable"] is False

    # Synthetic test mode (allow_synthetic=True) clearly identifies as synthetic
    synth_src = AuthoritativeContractSource(repository=repo, allow_synthetic=True)
    active_synth = synth_src.get_active_contract(datetime(2026, 9, 14, 10, 0, tzinfo=UTC))
    assert active_synth is not None
    assert active_synth.contract_id == "NIFTY-SYNTH-FUT"
    state_synth = synth_src.resolve_contract_state(datetime(2026, 9, 14, 10, 0, tzinfo=UTC))
    assert state_synth["is_synthetic"] is True
