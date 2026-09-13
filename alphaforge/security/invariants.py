"""
AlphaForge Security Invariants.

Executable forensic invariant assertions guaranteeing fail-closed personal trading safety.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from alphaforge.broker.models import BrokerOrderRequest
    from alphaforge.security.authorizer import SecurityAuthorizer
    from alphaforge.security.config import TradingModeConfig

from alphaforge.security.config import SecurityConfig
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    KillSwitchEngagedError,
    ReconciliationGateClosedError,
    SecretLeakError,
    SecurityAuthorizationError,
    SecurityConfigurationError,
)


def assert_live_trading_disabled_by_default(config: SecurityConfig | TradingModeConfig) -> None:
    """Invariant 1: Assert live trading is completely disabled by default."""
    mode_cfg = config.trading_mode_config if isinstance(config, SecurityConfig) else config
    if mode_cfg.trading_mode == TradingMode.LIVE:
        msg = "Security invariant violated: Default trading mode cannot be LIVE."
        raise SecurityConfigurationError(msg)
    if mode_cfg.live_trading_enabled:
        msg = "Security invariant violated: live_trading_enabled cannot be True by default."
        raise SecurityConfigurationError(msg)
    if mode_cfg.is_live_authorized:
        msg = "Security invariant violated: is_live_authorized must be False by default."
        raise SecurityConfigurationError(msg)


def assert_invalid_mode_fails_closed(raw_mode: Any) -> None:
    """Invariant 2: Assert invalid, typoed, or unknown trading modes fail closed immediately."""
    try:
        TradingMode.from_str(raw_mode)
    except SecurityConfigurationError:
        return
    except Exception as e:
        msg = (
            f"Expected SecurityConfigurationError on invalid mode {raw_mode!r}, "
            f"got {type(e).__name__}"
        )
        raise SecurityConfigurationError(msg) from e
    raise SecurityConfigurationError(
        f"Security invariant violated: Invalid mode {raw_mode!r} did not fail closed."
    )


def assert_no_secret_in_text(text: str, secret: str) -> None:
    """Invariant 3: Assert sensitive secret plaintext never appears in plain text or log line."""
    if not secret or len(secret) < 4:
        return
    if secret in text:
        msg = (
            f"Security invariant violated: "
            f"Secret pattern found in text payload (length: {len(text)})"
        )
        raise SecretLeakError(msg)


def assert_no_secret_in_persisted_state(state: Any, secret: str) -> None:
    """Invariant 4: Assert secrets never appear in serialized models or persisted strings."""
    if not secret or len(secret) < 4:
        return

    serialized = ""
    if isinstance(state, BaseModel):
        serialized = state.model_dump_json()
    elif isinstance(state, (dict, list)):
        serialized = json.dumps(state, default=str)
    else:
        serialized = str(state)

    if secret in serialized:
        msg = (
            f"Security invariant violated: "
            f"Secret detected in serialized state of {type(state).__name__}"
        )
        raise SecretLeakError(msg)


def assert_risk_controls_present(config: SecurityConfig) -> None:
    """Invariant 5: Assert mandatory risk controls cannot be disabled or bypassed."""
    if not config.enforce_risk_controls:
        msg = "Security invariant violated: enforce_risk_controls is False."
        raise SecurityConfigurationError(msg)


def assert_kill_switch_blocks_entry(
    authorizer: SecurityAuthorizer,
    request: BrokerOrderRequest,
) -> None:
    """Invariant 6: Assert that when kill switch is engaged, order submissions are blocked."""
    if not authorizer.kill_switch.is_engaged():
        msg = "Test setup error: Kill switch must be ENGAGED to test this invariant."
        raise SecurityAuthorizationError(msg)

    try:
        authorizer.authorize_order(request)
    except KillSwitchEngagedError:
        return
    except Exception as e:
        raise SecurityAuthorizationError(
            f"Expected KillSwitchEngagedError, got {type(e).__name__}"
        ) from e

    msg = "Security invariant violated: Order was accepted while kill switch was ENGAGED."
    raise SecurityAuthorizationError(msg)


def assert_reconciliation_gate_required(
    authorizer: SecurityAuthorizer,
    request: BrokerOrderRequest,
) -> None:
    """
    Invariant 7: Assert that when reconciliation gate is closed, order submissions are blocked.
    """
    if authorizer.reconciliation_gate is None or authorizer.reconciliation_gate.is_open:
        msg = "Test setup error: Reconciliation gate must be present and CLOSED."
        raise SecurityAuthorizationError(msg)

    try:
        authorizer.authorize_order(request)
    except ReconciliationGateClosedError:
        return
    except Exception as e:
        raise SecurityAuthorizationError(
            f"Expected ReconciliationGateClosedError, got {type(e).__name__}"
        ) from e

    msg = "Security invariant violated: Order was accepted while reconciliation gate was CLOSED."
    raise SecurityAuthorizationError(msg)
