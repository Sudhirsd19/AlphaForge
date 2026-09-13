"""
AlphaForge Phase 13 Security Invariant Test Suite.

Verifies correct execution, assertion logic, and failure handling across all 7 security invariants.
"""

import pytest

from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.security.authorizer import SecurityAuthorizer
from alphaforge.security.config import SecurityConfig, TradingModeConfig
from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import (
    SecretLeakError,
    SecurityAuthorizationError,
    SecurityConfigurationError,
)
from alphaforge.security.invariants import (
    assert_invalid_mode_fails_closed,
    assert_kill_switch_blocks_entry,
    assert_live_trading_disabled_by_default,
    assert_no_secret_in_persisted_state,
    assert_no_secret_in_text,
    assert_reconciliation_gate_required,
    assert_risk_controls_present,
)
from alphaforge.security.kill_switch import KillSwitch


def _make_req() -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="ORD-INV-001",
        symbol="NIFTY26MARFUT",
        side=OrderSide.BUY,
        quantity=50,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
    )


def test_inv1_assert_live_trading_disabled_by_default() -> None:
    """Invariant 1: Paper mode and disabled live trading by default."""
    cfg = SecurityConfig()
    assert_live_trading_disabled_by_default(cfg)

    # Inverted condition raises SecurityConfigurationError
    cfg_live = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    with pytest.raises(SecurityConfigurationError):
        assert_live_trading_disabled_by_default(cfg_live)


def test_inv2_assert_invalid_mode_fails_closed() -> None:
    """Invariant 2: Assert invalid trading modes fail closed."""
    assert_invalid_mode_fails_closed("garbage_mode")
    assert_invalid_mode_fails_closed("livee")
    assert_invalid_mode_fails_closed("")

    # If valid mode passed, assertion fails (because it was supposed to fail closed)
    with pytest.raises(SecurityConfigurationError):
        assert_invalid_mode_fails_closed("PAPER")


def test_inv3_assert_no_secret_in_text() -> None:
    """Invariant 3: Assert secrets never leak in plain text."""
    secret = "MySecretKeyXYZ"  # noqa: S105
    safe_text = "Logging information without sensitive data"
    leak_text = f"Connecting with {secret} on broker"

    assert_no_secret_in_text(safe_text, secret)

    with pytest.raises(SecretLeakError):
        assert_no_secret_in_text(leak_text, secret)


def test_inv4_assert_no_secret_in_persisted_state() -> None:
    """Invariant 4: Assert secrets never leak in serialized state."""
    secret = "SuperConfidentialKey99"  # noqa: S105
    safe_dict = {"status": "ACTIVE", "orders": 10}
    leak_dict = {"status": "ACTIVE", "token": secret}

    assert_no_secret_in_persisted_state(safe_dict, secret)

    with pytest.raises(SecretLeakError):
        assert_no_secret_in_persisted_state(leak_dict, secret)


def test_inv5_assert_risk_controls_present() -> None:
    """Invariant 5: Assert risk controls present."""
    cfg = SecurityConfig()
    assert_risk_controls_present(cfg)


def test_inv6_assert_kill_switch_blocks_entry() -> None:
    """Invariant 6: Assert kill switch blocks entry."""
    ks = KillSwitch(initial_status=KillSwitchStatus.ENGAGED)
    cfg = SecurityConfig()
    authorizer = SecurityAuthorizer(security_config=cfg, kill_switch=ks)
    req = _make_req()

    assert_kill_switch_blocks_entry(authorizer, req)

    # Disarmed kill switch should cause invariant assertion to fail
    ks.disarm("Ready")
    with pytest.raises(SecurityAuthorizationError):
        assert_kill_switch_blocks_entry(authorizer, req)


def test_inv7_assert_reconciliation_gate_required() -> None:
    """Invariant 7: Assert reconciliation gate required."""
    gate = ReconciliationGate(initially_open=False)
    cfg = SecurityConfig()
    authorizer = SecurityAuthorizer(
        security_config=cfg,
        reconciliation_gate=gate,
    )
    req = _make_req()

    assert_reconciliation_gate_required(authorizer, req)
