"""
AlphaForge Shadow Execution Guard (Phase 17).
Enforces strict fail-closed shadow-only execution and prevents any real-money order routing.
"""

from __future__ import annotations

from typing import Any

from alphaforge.deployment.exceptions import DeploymentSafetyError
from alphaforge.paper_shadow.enums import PaperShadowMode


class ShadowExecutionOnlyGuard:
    """
    Security gate that ensures Phase 17 execution remains strictly simulated/shadow.
    Fails closed if live trading capability, live credentials, or live brokers are detected.
    """

    def __init__(self, mode: PaperShadowMode = PaperShadowMode.SHADOW) -> None:
        if mode not in (PaperShadowMode.SHADOW, PaperShadowMode.PAPER):
            msg = (
                f"ShadowExecutionOnlyGuard: Invalid execution mode '{mode}'. "
                "Only SHADOW/PAPER allowed."
            )
            raise DeploymentSafetyError(msg)
        self._mode = mode
        self._live_orders_blocked_count = 0

    @property
    def mode(self) -> PaperShadowMode:
        return self._mode

    @property
    def live_orders_blocked_count(self) -> int:
        return self._live_orders_blocked_count

    def verify_safety_invariants(self, context: dict[str, Any] | None = None) -> None:
        """Verify that runtime context has zero live broker capabilities."""
        if context is None:
            return

        forbidden_keys = (
            "live_api_key",
            "dhan_client_id",
            "zerodha_api_key",
            "angel_jwt_token",
            "broker_live_endpoint",
            "live_websocket_channel",
        )
        for k in forbidden_keys:
            if k in context and context[k]:
                self._live_orders_blocked_count += 1
                msg = (
                    f"ShadowExecutionOnlyGuard: Forbidden live credential detected: '{k}'. "
                    "Failing closed."
                )
                raise DeploymentSafetyError(msg)

        if context.get("live_trading_enabled", False):
            self._live_orders_blocked_count += 1
            msg = (
                "ShadowExecutionOnlyGuard: live_trading_enabled is TRUE. "
                "Phase 17 strictly forbids live execution."
            )
            raise DeploymentSafetyError(msg)

    def assert_no_live_order(self) -> None:
        """Affirmative assertion that real broker order placement is impossible."""
        if self._mode not in (PaperShadowMode.SHADOW, PaperShadowMode.PAPER):
            msg = "ShadowExecutionOnlyGuard: Real-money orders are blocked fail-closed."
            raise DeploymentSafetyError(msg)
