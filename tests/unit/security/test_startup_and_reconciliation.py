"""
AlphaForge Phase 13 Security Tests: Startup Verification & Reconciliation Gate.

Verifies SEC10 and ADV-SEC-7.
"""

import contextlib
from datetime import UTC, datetime

import pytest

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)
from alphaforge.security.authorizer import SecurityAuthorizer
from alphaforge.security.config import SecurityConfig, TradingModeConfig
from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    ReconciliationGateClosedError,
)
from alphaforge.security.invariants import assert_reconciliation_gate_required
from alphaforge.security.startup import SecurityStartupGate


def _make_order_request() -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="ORD-LIVE-001",
        symbol="NIFTY26MARFUT",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
    )


def _make_matched_result() -> ReconciliationResult:
    return ReconciliationResult(
        reconciliation_id="REC-2026-001",
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


def _make_mismatched_result() -> ReconciliationResult:
    return ReconciliationResult(
        reconciliation_id="REC-2026-002",
        timestamp=datetime.now(UTC),
        status=ReconciliationStatus.MISMATCH,
        local_order_count=1,
        broker_order_count=0,
        local_position_count=0,
        broker_position_count=0,
        matched_count=0,
        mismatch_count=1,
        unknown_count=0,
        new_entries_allowed=False,
        manual_escalation_required=True,
        reason_code=ReconciliationReasonCode.BROKER_ORDER_MISSING,
    )


def test_sec10_reconciliation_gate_blocks_trading_until_complete() -> None:
    """SEC10: Reconciliation gate blocks trade authorization until cleanly matched."""
    cfg = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    store = CredentialStore()
    store.set_live_credentials(
        BrokerCredentials(
            broker_id="zerodha",
            api_key=SecretValue("ProductionKeyABC987"),
            api_secret=SecretValue("ProductionSecretXYZ987"),
            account_id="ACC_PROD_1",
            is_live=True,
        )
    )

    gate = ReconciliationGate(initially_open=False)
    startup_gate = SecurityStartupGate(
        security_config=cfg,
        credential_store=store,
        reconciliation_gate=gate,
    )

    report = startup_gate.verify_startup()
    assert report.passed is True
    assert report.reconciliation_gate_verified is True

    # Gate is still closed, ready to trade should raise
    with pytest.raises(ReconciliationGateClosedError):
        startup_gate.assert_ready_to_trade()

    authorizer = SecurityAuthorizer(
        security_config=cfg,
        credential_store=store,
        reconciliation_gate=gate,
        startup_gate=startup_gate,
    )

    req = _make_order_request()
    assert_reconciliation_gate_required(authorizer, req)

    # Open the gate with verified matched reconciliation
    gate.open(_make_matched_result())
    assert gate.is_open is True

    # Now ready to trade and order authorization succeed
    startup_gate.assert_ready_to_trade()
    authorizer.authorize_order(req)


def test_adv_sec7_restart_live_with_incomplete_reconciliation_blocks_trading() -> None:
    """ADV-SEC-7: System restart in LIVE mode blocks trading if reconciliation incomplete."""
    cfg = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    store = CredentialStore()
    store.set_live_credentials(
        BrokerCredentials(
            broker_id="zerodha",
            api_key=SecretValue("ProductionKeyABC987"),
            api_secret=SecretValue("ProductionSecretXYZ987"),
            account_id="ACC_PROD_1",
            is_live=True,
        )
    )

    # On restart, gate starts closed
    gate = ReconciliationGate(initially_open=False)
    authorizer = SecurityAuthorizer(
        security_config=cfg,
        credential_store=store,
        reconciliation_gate=gate,
    )

    req = _make_order_request()

    # Cold boot attempt before reconciliation -> BLOCKED
    with pytest.raises(ReconciliationGateClosedError):
        authorizer.authorize_order(req)

    # Incomplete/mismatch reconciliation pass executed
    with contextlib.suppress(Exception):
        gate.open(_make_mismatched_result())

    assert not gate.is_open

    # Trade submission STILL BLOCKED
    with pytest.raises(ReconciliationGateClosedError):
        authorizer.authorize_order(req)
