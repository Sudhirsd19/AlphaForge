"""
AlphaForge Phase 13 Security Tests: End-to-End Secure Broker & Order Authorization.

Verifies SEC15: Full order authorization pipeline with SecureBroker wrapping PaperBroker.
"""

from datetime import UTC, datetime

import pytest

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.broker.paper import PaperBroker
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)
from alphaforge.security.authorizer import SecureBroker, SecurityAuthorizer
from alphaforge.security.config import SecurityConfig, TradingModeConfig
from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import (
    KillSwitchEngagedError,
    ReconciliationGateClosedError,
    SecurityAuthorizationError,
)
from alphaforge.security.kill_switch import KillSwitch
from alphaforge.security.startup import SecurityStartupGate


def _make_order_request(client_order_id: str = "ORD-SEC-001") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        symbol="NIFTY26MARFUT",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
    )


def _make_matched_result() -> ReconciliationResult:
    return ReconciliationResult(
        reconciliation_id="REC-E2E-001",
        timestamp=datetime.now(UTC),
        status=ReconciliationStatus.MATCHED,
        local_order_count=0,
        broker_order_count=0,
        local_position_count=0,
        broker_position_count=0,
        matched_count=0,
        mismatch_count=0,
        unknown_count=0,
        new_entries_allowed=True,
        manual_escalation_required=False,
        reason_code=ReconciliationReasonCode.MATCHED,
    )


def test_sec15_paper_trading_e2e_authorization() -> None:
    """SEC15: Paper mode orders are authorized, executed by PaperBroker, and tracked."""
    cfg = SecurityConfig()  # Default PAPER
    paper_broker = PaperBroker()
    kill_switch = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    rec_gate = ReconciliationGate(initially_open=True)

    startup_gate = SecurityStartupGate(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
    )
    startup_gate.verify_startup()

    authorizer = SecurityAuthorizer(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
        startup_gate=startup_gate,
    )

    secure_broker = SecureBroker(delegate=paper_broker, authorizer=authorizer)

    req = _make_order_request("ORD-PAPER-001")
    order = secure_broker.submit_order(req)

    assert order.client_order_id == "ORD-PAPER-001"
    assert paper_broker.get_order(client_order_id="ORD-PAPER-001") is not None
    assert secure_broker.get_order(client_order_id="ORD-PAPER-001") is not None
    assert secure_broker.is_available() is True


def test_sec15_kill_switch_blocks_secure_broker_submission() -> None:
    """SEC15: Kill switch engagement blocks order submission through SecureBroker."""
    cfg = SecurityConfig()
    paper_broker = PaperBroker()
    kill_switch = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    rec_gate = ReconciliationGate(initially_open=True)

    startup_gate = SecurityStartupGate(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
    )
    startup_gate.verify_startup()

    authorizer = SecurityAuthorizer(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
        startup_gate=startup_gate,
    )
    secure_broker = SecureBroker(delegate=paper_broker, authorizer=authorizer)

    # Submit first order while disarmed
    req1 = _make_order_request("ORD-KS-001")
    secure_broker.submit_order(req1)

    # Engage kill switch
    kill_switch.engage("Rapid market anomaly detected", operator="circuit_breaker")

    # Attempt second order -> MUST FAIL CLOSED
    req2 = _make_order_request("ORD-KS-002")
    with pytest.raises(KillSwitchEngagedError):
        secure_broker.submit_order(req2)

    # Underlying broker must NEVER have received ORD-KS-002
    assert paper_broker.get_order(client_order_id="ORD-KS-002") is None

    # Cancellation of resting order ORD-KS-001 is permitted
    cancelled_order = secure_broker.cancel_order("ORD-KS-001")
    assert cancelled_order.client_order_id == "ORD-KS-001"


def test_sec15_reconciliation_gate_blocks_secure_broker_submission() -> None:
    """SEC15: Closed reconciliation gate blocks order submission through SecureBroker."""
    cfg = SecurityConfig()
    paper_broker = PaperBroker()
    kill_switch = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    rec_gate = ReconciliationGate(initially_open=False)

    startup_gate = SecurityStartupGate(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
    )
    startup_gate.verify_startup()

    authorizer = SecurityAuthorizer(
        security_config=cfg,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
        startup_gate=startup_gate,
    )
    secure_broker = SecureBroker(delegate=paper_broker, authorizer=authorizer)

    req = _make_order_request("ORD-REC-001")
    with pytest.raises(ReconciliationGateClosedError):
        secure_broker.submit_order(req)

    assert paper_broker.get_order(client_order_id="ORD-REC-001") is None

    # Open gate
    rec_gate.open(_make_matched_result())

    # Now submission succeeds
    order = secure_broker.submit_order(req)
    assert order.client_order_id == "ORD-REC-001"
    assert paper_broker.get_order(client_order_id="ORD-REC-001") is not None


def test_sec15_live_mode_dual_opt_in_and_credential_enforcement() -> None:
    """SEC15: Live mode submissions strictly enforce dual opt-in and live credentials."""
    # Attempt live mode without live credentials
    cfg_live = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    paper_broker = PaperBroker()
    kill_switch = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    rec_gate = ReconciliationGate(initially_open=True)
    empty_store = CredentialStore()

    authorizer = SecurityAuthorizer(
        security_config=cfg_live,
        credential_store=empty_store,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
    )
    secure_broker = SecureBroker(delegate=paper_broker, authorizer=authorizer)

    req = _make_order_request("ORD-LIVE-E2E-001")
    with pytest.raises(SecurityAuthorizationError) as exc_info:
        secure_broker.submit_order(req)
    assert "Live credentials are not configured" in str(exc_info.value)
    assert paper_broker.get_order(client_order_id="ORD-LIVE-E2E-001") is None

    # Now provide authentic live credentials
    live_store = CredentialStore()
    live_store.set_live_credentials(
        BrokerCredentials(
            broker_id="zerodha",
            api_key=SecretValue("ProductionKeyABC987"),
            api_secret=SecretValue("ProductionSecretXYZ987"),
            account_id="PROD_ACC_100",
            is_live=True,
        )
    )

    live_authorizer = SecurityAuthorizer(
        security_config=cfg_live,
        credential_store=live_store,
        kill_switch=kill_switch,
        reconciliation_gate=rec_gate,
    )
    live_secure_broker = SecureBroker(delegate=paper_broker, authorizer=live_authorizer)

    # Now order is authorized and routes to broker
    order = live_secure_broker.submit_order(req)
    assert order.client_order_id == "ORD-LIVE-E2E-001"
