"""
AlphaForge Deployment Readiness Diagnostics.

Provides passive, read-only pre-flight readiness evaluation.
Guarantees:
- PAPER and SHADOW never require or probe a real production/live broker.
- Only LIVE requires live broker connectivity and live credentials.
- Readiness is strictly diagnostic (READ/RECORD) and NEVER submits, cancels, or mutates state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from alphaforge.broker.interface import AbstractBroker
    from alphaforge.deployment.config import DeploymentConfig
    from alphaforge.security.startup import SecurityStartupGate
from alphaforge.deployment.broker_guard import is_live_execution_broker
from alphaforge.deployment.enums import DeploymentEnvironment


class ReadinessReport(BaseModel):
    """Immutable diagnostic report of deployment readiness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: DeploymentEnvironment
    is_ready: bool = Field(description="Whether runtime environment is fully ready to operate.")
    config_valid: bool
    directories_ready: bool
    broker_ready: bool
    credentials_ready: bool
    security_ready: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    details: str = Field(default="")


class DeploymentReadinessChecker:
    """
    Purely diagnostic pre-flight readiness checker.

    Invariants:
    - Never submits or cancels orders.
    - Never alters risk limits or position state.
    - PAPER and SHADOW runs never probe production brokers.
    """

    def __init__(
        self,
        config: DeploymentConfig,
        broker: AbstractBroker | None = None,
        security_startup_gate: SecurityStartupGate | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._security_startup_gate = security_startup_gate

    def check_readiness(self) -> ReadinessReport:
        """
        Execute passive diagnostic probes and return a ReadinessReport.
        """
        env = self._config.environment
        checks: dict[str, bool] = {}

        # 1. Configuration Check
        config_valid = True
        try:
            # Check for path safety
            _ = self._config.effective_state_dir
            _ = self._config.effective_log_dir
            _ = self._config.effective_audit_dir
            _ = self._config.effective_observability_dir
            checks["config_valid"] = True
        except Exception:
            config_valid = False
            checks["config_valid"] = False

        # 2. Runtime Directories Check
        directories_ready = True
        try:
            for p in (
                self._config.effective_state_dir,
                self._config.effective_log_dir,
                self._config.effective_audit_dir,
                self._config.effective_observability_dir,
            ):
                if not p.exists():
                    directories_ready = False
                    break
            checks["directories_ready"] = directories_ready
        except Exception:
            directories_ready = False
            checks["directories_ready"] = False

        # 3. Broker Diagnostic Check
        # Strict Rule: PAPER and SHADOW never probe live broker
        broker_ready = True
        if env == DeploymentEnvironment.LIVE:
            if self._broker is None:
                broker_ready = False
            else:
                # In LIVE, broker must be available and have live capability
                try:
                    broker_ready = self._broker.is_available() and is_live_execution_broker(
                        self._broker
                    )
                except Exception:
                    broker_ready = False
        else:
            # For PAPER, SHADOW, DEV, TEST:
            # Never probe live broker. If a broker is attached, verify it is NOT a live broker
            if self._broker is not None:
                if is_live_execution_broker(self._broker):
                    # Live broker attached to non-live environment is unsafe
                    broker_ready = False
                else:
                    try:
                        broker_ready = self._broker.is_available()
                    except Exception:
                        broker_ready = False
            else:
                broker_ready = True
        checks["broker_ready"] = broker_ready

        # 4. Credentials Check
        credentials_ready = True
        if env == DeploymentEnvironment.LIVE:
            # In LIVE, credential source must be explicitly configured
            credentials_ready = bool(self._config.credential_source)
        else:
            # In non-LIVE, real credentials are not required
            credentials_ready = True
        checks["credentials_ready"] = credentials_ready

        # 5. Security Gates Check
        security_ready = True
        if env == DeploymentEnvironment.LIVE:
            if not self._config.live_authorized:
                security_ready = False
            elif self._security_startup_gate is not None:
                security_ready = self._security_startup_gate.is_verified
            else:
                security_ready = True
        else:
            security_ready = True
        checks["security_ready"] = security_ready

        all_passed = (
            config_valid
            and directories_ready
            and broker_ready
            and credentials_ready
            and security_ready
        )

        details = (
            f"Readiness check completed for '{env.value}'. "
            f"Result: {'READY' if all_passed else 'NOT_READY'}."
        )

        return ReadinessReport(
            environment=env,
            is_ready=all_passed,
            config_valid=config_valid,
            directories_ready=directories_ready,
            broker_ready=broker_ready,
            credentials_ready=credentials_ready,
            security_ready=security_ready,
            checks=checks,
            details=details,
        )
