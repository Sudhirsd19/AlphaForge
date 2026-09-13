"""
AlphaForge Phase 13 Security Tests: Kill Switch & Risk Control Invariants.

Verifies SEC8, SEC9, SEC12, and ADV-SEC-5, ADV-SEC-6.
"""

import threading
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.security.authorizer import SecurityAuthorizer
from alphaforge.security.config import SecurityConfig
from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import (
    KillSwitchEngagedError,
    SecurityConfigurationError,
)
from alphaforge.security.invariants import (
    assert_kill_switch_blocks_entry,
    assert_risk_controls_present,
)
from alphaforge.security.kill_switch import KillSwitch


def _make_order_request(client_order_id: str = "ORD-TEST-001") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        symbol="NIFTY26MARFUT",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
    )


def test_sec8_kill_switch_blocks_new_entries() -> None:
    """SEC8: Kill switch transitions correctly and blocks new order submissions."""
    ks = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    assert ks.is_disarmed()
    assert not ks.is_engaged()
    ks.assert_disarmed()

    # Engage kill switch
    ks.engage("Emergency market volatility anomaly", operator="risk_sentinel")
    assert ks.is_engaged()
    assert not ks.is_disarmed()
    assert ks.status == KillSwitchStatus.ENGAGED

    with pytest.raises(KillSwitchEngagedError) as exc_info:
        ks.assert_disarmed()
    assert "Emergency market volatility anomaly" in str(exc_info.value)

    # Invariant assertion via authorizer
    cfg = SecurityConfig()
    authorizer = SecurityAuthorizer(security_config=cfg, kill_switch=ks)
    request = _make_order_request()
    assert_kill_switch_blocks_entry(authorizer, request)

    # Disarm kill switch
    ks.disarm("Market returned to normal parameters", operator="admin")
    assert ks.is_disarmed()
    ks.assert_disarmed()

    # Audit trail verification
    trail = ks.get_audit_trail()
    assert len(trail) >= 3
    assert trail[-2].action == "ENGAGE"
    assert trail[-1].action == "DISARM"


def test_sec9_risk_protection_cannot_be_disabled() -> None:
    """SEC9: Attempting to disable risk controls fails closed."""
    cfg = SecurityConfig()
    assert_risk_controls_present(cfg)

    # Direct False boolean
    with pytest.raises(SecurityConfigurationError):
        SecurityConfig(enforce_risk_controls=False)  # type: ignore[arg-type]

    # String falsy representation
    with pytest.raises(SecurityConfigurationError):
        SecurityConfig(enforce_risk_controls="false")  # type: ignore[arg-type]

    with pytest.raises(SecurityConfigurationError):
        SecurityConfig(enforce_risk_controls="0")  # type: ignore[arg-type]


def test_sec12_security_config_survives_restart() -> None:
    """SEC12: Security configuration survives serialization and restart identically."""
    env_vars = {
        "TRADING_MODE": "SHADOW",
        "KILL_SWITCH_STATUS": "DISARMED",
        "ENFORCE_RISK_CONTROLS": "true",
        "ENFORCE_RECONCILIATION_GATE": "true",
        "REDACT_LOGS": "true",
        "MAX_DAILY_LOSS_LIMIT": "50000.00",
    }

    config1 = SecurityConfig.from_env(env_vars)
    assert config1.trading_mode_config.trading_mode == TradingMode.SHADOW
    assert config1.kill_switch_initial_status == KillSwitchStatus.DISARMED
    assert config1.max_daily_loss_limit == Decimal("50000.00")

    # Simulate restart via dumped dictionary
    data = config1.model_dump()
    config2 = SecurityConfig(**data)
    assert config2.trading_mode_config.trading_mode == config1.trading_mode_config.trading_mode
    assert config2.kill_switch_initial_status == config1.kill_switch_initial_status
    assert config2.max_daily_loss_limit == config1.max_daily_loss_limit
    assert config2.enforce_risk_controls == config1.enforce_risk_controls


def test_adv_sec5_malformed_daily_loss_fails_closed() -> None:
    """ADV-SEC-5: Attempt to disable or corrupt daily loss protection fails closed."""
    # Zero limit
    with pytest.raises(SecurityConfigurationError):
        SecurityConfig(max_daily_loss_limit=Decimal("0"))

    # Negative limit
    with pytest.raises(SecurityConfigurationError):
        SecurityConfig(max_daily_loss_limit=Decimal("-1000"))

    # Malformed decimal
    with pytest.raises((SecurityConfigurationError, ValidationError)):
        SecurityConfig.from_env({"MAX_DAILY_LOSS_LIMIT": "unlimited"})


def test_adv_sec6_enable_kill_switch_immediately_before_order() -> None:
    """ADV-SEC-6: Enable kill switch concurrently right before order creation -> order blocked."""
    ks = KillSwitch(initial_status=KillSwitchStatus.DISARMED)
    cfg = SecurityConfig()
    authorizer = SecurityAuthorizer(security_config=cfg, kill_switch=ks)

    order_rejected = []
    order_accepted = []

    def submit_order_thread() -> None:
        try:
            req = _make_order_request("ORD-RACE-001")
            authorizer.authorize_order(req)
            order_accepted.append(True)
        except KillSwitchEngagedError:
            order_rejected.append(True)

    # Immediately engage kill switch
    ks.engage("Pre-emptive halt", operator="circuit_breaker")

    t = threading.Thread(target=submit_order_thread)
    t.start()
    t.join()

    assert len(order_rejected) == 1
    assert len(order_accepted) == 0
