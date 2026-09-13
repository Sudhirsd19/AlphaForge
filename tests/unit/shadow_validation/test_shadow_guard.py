"""
Unit tests for ShadowExecutionOnlyGuard (Phase 17).
Verifies strict shadow execution, rejection of live credentials, and fail-closed safety.
"""

from __future__ import annotations

import pytest

from alphaforge.deployment.exceptions import DeploymentSafetyError
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.shadow_validation.shadow_guard import ShadowExecutionOnlyGuard


def test_guard_initialization_allowed_modes() -> None:
    guard_shadow = ShadowExecutionOnlyGuard(mode=PaperShadowMode.SHADOW)
    assert guard_shadow.mode == PaperShadowMode.SHADOW

    guard_paper = ShadowExecutionOnlyGuard(mode=PaperShadowMode.PAPER)
    assert guard_paper.mode == PaperShadowMode.PAPER


def test_guard_rejects_forbidden_credentials() -> None:
    guard = ShadowExecutionOnlyGuard(mode=PaperShadowMode.SHADOW)

    # Clean context
    guard.verify_safety_invariants({"execution_mode": "SHADOW"})
    guard.assert_no_live_order()

    # Context with live credentials
    with pytest.raises(DeploymentSafetyError, match="Forbidden live credential detected"):
        guard.verify_safety_invariants({"live_api_key": "LIVE_SECRET_KEY_12345"})

    with pytest.raises(DeploymentSafetyError, match="Forbidden live credential detected"):
        guard.verify_safety_invariants({"dhan_client_id": "DHAN10099"})

    with pytest.raises(DeploymentSafetyError, match="live_trading_enabled is TRUE"):
        guard.verify_safety_invariants({"live_trading_enabled": True})

    assert guard.live_orders_blocked_count >= 3
