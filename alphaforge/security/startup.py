"""
AlphaForge Security Startup Gate.

Coordinated pre-flight verification sequence enforcing fail-closed personal trading safety.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphaforge.reconciliation.gate import ReconciliationGate
    from alphaforge.security.config import SecurityConfig

from pydantic import BaseModel, ConfigDict, Field

from alphaforge.security.credentials import CredentialStore
from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import (
    CredentialIsolationError,
    MissingCredentialsError,
    ReconciliationGateClosedError,
    SecurityStartupError,
)
from alphaforge.security.kill_switch import KillSwitch


class StartupSecurityReport(BaseModel):
    """Immutable audit report of startup security verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_mode: TradingMode
    kill_switch_status: KillSwitchStatus
    credentials_verified: bool
    risk_controls_verified: bool
    reconciliation_gate_verified: bool
    passed: bool = Field(default=False)
    details: str = Field(default="")


class SecurityStartupGate:
    """
    Coordinates safe startup verification sequence before trading can commence.

    Guarantees:
    1. Trading mode is unambiguously resolved (paper by default).
    2. Dual opt-in verified if LIVE mode requested.
    3. Credentials valid, authentic, and isolated.
    4. Kill switch verified disarmed.
    5. Risk controls active and intact.
    6. Reconciliation gate enforced.
    """

    def __init__(
        self,
        security_config: SecurityConfig,
        credential_store: CredentialStore | None = None,
        kill_switch: KillSwitch | None = None,
        reconciliation_gate: ReconciliationGate | None = None,
        risk_config: Any | None = None,
    ) -> None:
        self._config = security_config
        self._credential_store = credential_store or CredentialStore()
        init_status = security_config.kill_switch_initial_status
        self._kill_switch = kill_switch or KillSwitch(initial_status=init_status)
        self._reconciliation_gate = reconciliation_gate
        self._risk_config = risk_config
        self._is_verified: bool = False
        self._latest_report: StartupSecurityReport | None = None

    @property
    def is_verified(self) -> bool:
        return self._is_verified

    @property
    def latest_report(self) -> StartupSecurityReport | None:
        return self._latest_report

    def verify_startup(self) -> StartupSecurityReport:
        """
        Execute pre-flight security checks.

        Fails closed with SecurityStartupError on any violation.
        """
        mode = self._config.trading_mode_config.effective_mode

        # Step 1: Trading Mode & Dual Opt-in Validation
        if mode == TradingMode.LIVE and not self._config.trading_mode_config.is_live_authorized:
            msg = (
                "Startup failed: LIVE mode requested but dual authorization "
                "(live_trading_enabled=True) is not met."
            )
            raise SecurityStartupError(msg)

        # Step 2: Credential Verification & Isolation
        credentials_verified = False
        if mode == TradingMode.LIVE:
            try:
                live_creds = self._credential_store.get_credentials(TradingMode.LIVE)
                if not live_creds.is_live:
                    msg = "Startup failed: Non-live credentials found in live credential slot."
                    raise SecurityStartupError(msg)
                credentials_verified = True
            except (MissingCredentialsError, CredentialIsolationError) as e:
                raise SecurityStartupError(f"Startup failed: Invalid live credentials - {e}") from e
        else:
            # Paper / Shadow mode
            credentials_verified = True

        # Step 3: Kill Switch Verification
        if self._kill_switch.is_engaged():
            raise SecurityStartupError(
                f"Startup failed: Kill switch is ENGAGED ({self._kill_switch.latest_reason})."
            )

        # Step 4: Risk Controls Verification
        if not self._config.enforce_risk_controls:
            msg = "Startup failed: Risk controls are disabled in configuration."
            raise SecurityStartupError(msg)
        risk_controls_verified = True

        # Step 5: Reconciliation Gate Verification
        reconciliation_gate_verified = False
        if self._config.enforce_reconciliation_gate:
            if mode == TradingMode.LIVE and self._reconciliation_gate is None:
                msg = "Startup failed: LIVE trading requires an active ReconciliationGate."
                raise SecurityStartupError(msg)
            reconciliation_gate_verified = True

        report = StartupSecurityReport(
            trading_mode=mode,
            kill_switch_status=self._kill_switch.status,
            credentials_verified=credentials_verified,
            risk_controls_verified=risk_controls_verified,
            reconciliation_gate_verified=reconciliation_gate_verified,
            passed=True,
            details=f"Security startup gate passed successfully for mode '{mode.value}'.",
        )

        self._is_verified = True
        self._latest_report = report
        return report

    def assert_ready_to_trade(self) -> None:
        """
        Assert that startup checks passed and reconciliation gate is currently open.

        Raises SecurityStartupError or ReconciliationGateClosedError if not ready.
        """
        if not self._is_verified:
            msg = "Trading blocked: Security startup verification has not been performed."
            raise SecurityStartupError(msg)

        self._kill_switch.assert_disarmed()

        if self._reconciliation_gate is not None and not self._reconciliation_gate.is_open:
            msg = (
                f"Trading blocked: Reconciliation gate is closed "
                f"({self._reconciliation_gate.reason})."
            )
            raise ReconciliationGateClosedError(msg)
