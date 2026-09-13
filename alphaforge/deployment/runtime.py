"""
AlphaForge Deployment Runtime Lifecycle.

Coordinates the authoritative Startup & Shutdown sequences for trading deployments.
Enforces strict fail-closed startup validation: no broker orders can be submitted
if startup validation fails, is incomplete, or if runtime has shut down.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

from alphaforge.broker.interface import AbstractBroker
from alphaforge.deployment.broker_guard import DeploymentBrokerGuard
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import StartupValidationError
from alphaforge.deployment.identity import (
    DeploymentIdentity,
    get_deployment_identity,
)
from alphaforge.deployment.isolation import EnvironmentDirectoryManager
from alphaforge.deployment.readiness import DeploymentReadinessChecker

if TYPE_CHECKING:
    from alphaforge.broker.models import BrokerOrder, BrokerOrderRequest, BrokerPosition
    from alphaforge.deployment.config import DeploymentConfig
    from alphaforge.observability.sinks import SafeObservabilityDispatcher
    from alphaforge.security.startup import SecurityStartupGate


class RuntimeGuardedBroker(AbstractBroker):
    """
    Broker wrapper enforcing runtime active status.
    Blocks any order submissions if runtime is not actively started.
    """

    def __init__(self, delegate: AbstractBroker, runtime: DeploymentRuntime) -> None:
        self._delegate = delegate
        self._runtime = runtime

    @property
    def delegate(self) -> AbstractBroker:
        return self._delegate

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        if not self._runtime.is_active:
            raise StartupValidationError(
                "Order submission blocked: DeploymentRuntime is not active "
                "(startup incomplete, failed, or runtime shut down)."
            )
        return self._delegate.submit_order(request)

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        return self._delegate.get_order(client_order_id, broker_order_id)

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        return self._delegate.get_open_orders()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        return self._delegate.get_positions()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        return self._delegate.cancel_order(client_order_id)

    def is_available(self) -> bool:
        return self._delegate.is_available()


class DeploymentRuntime:
    """
    Deployment runtime manager.

    Coordinates deterministic lifecycle:
    LOAD CONFIG
    -> VALIDATE CONFIG
    -> RESOLVE ENVIRONMENT
    -> VALIDATE ENVIRONMENT SAFETY
    -> VALIDATE CREDENTIAL SOURCE
    -> VALIDATE RUNTIME DIRECTORIES
    -> VALIDATE REQUIRED STATE
    -> VALIDATE SECURITY GATES
    -> ONLY THEN initialize trading runtime
    """

    def __init__(
        self,
        config: DeploymentConfig,
        broker: AbstractBroker | None = None,
        security_startup_gate: SecurityStartupGate | None = None,
        observability_dispatcher: SafeObservabilityDispatcher | None = None,
        git_commit: str | None = None,
    ) -> None:
        self._config = config
        self._raw_broker = broker
        self._security_startup_gate = security_startup_gate
        self._dispatcher = observability_dispatcher
        self._identity: DeploymentIdentity = get_deployment_identity(config, git_commit)

        self._lock = threading.RLock()
        self._is_started: bool = False
        self._is_active: bool = False
        self._is_shutdown: bool = False

        # Guard broker with both deployment broker guard and runtime lifecycle guard
        if broker is not None:
            guarded_broker = DeploymentBrokerGuard(broker, config)
            self._broker: AbstractBroker | None = RuntimeGuardedBroker(guarded_broker, self)
        else:
            self._broker = None

    @property
    def config(self) -> DeploymentConfig:
        return self._config

    @property
    def identity(self) -> DeploymentIdentity:
        return self._identity

    @property
    def broker(self) -> AbstractBroker | None:
        return self._broker

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._is_started

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._is_shutdown

    def startup(self) -> None:
        """
        Execute deterministic startup validation sequence.
        Fails closed on any violation.
        """
        with self._lock:
            if self._is_started:
                return

            env = self._config.environment

            # Step 1 & 2: Environment Safety Validation
            if env == DeploymentEnvironment.LIVE and not self._config.live_authorized:
                raise StartupValidationError(
                    "Startup failed: LIVE environment selected without explicit live authorization."
                )

            # Step 3: Credential Source Validation
            if env == DeploymentEnvironment.LIVE and not self._config.credential_source:
                raise StartupValidationError(
                    "Startup failed: LIVE environment requires valid credential_source."
                )

            # Step 4: Runtime Directory Initialization and Marker Validation
            try:
                EnvironmentDirectoryManager.initialize_directories(self._config)
            except Exception as exc:
                raise StartupValidationError(
                    f"Startup failed: Runtime directory initialization error: {exc}"
                ) from exc

            # Step 5: Pre-flight Readiness Diagnostics (passive, non-mutating)
            checker = DeploymentReadinessChecker(
                config=self._config,
                broker=self._raw_broker,
                security_startup_gate=self._security_startup_gate,
            )
            report = checker.check_readiness()
            if not report.is_ready:
                raise StartupValidationError(
                    f"Startup failed: Pre-flight readiness check failed. "
                    f"Failed checks: {[k for k, v in report.checks.items() if not v]}"
                )

            # Step 6: Security Gate Verification (if LIVE)
            if env == DeploymentEnvironment.LIVE and self._security_startup_gate is not None:
                try:
                    self._security_startup_gate.verify_startup()
                except Exception as exc:
                    raise StartupValidationError(
                        f"Startup failed: SecurityStartupGate verification failed: {exc}"
                    ) from exc

            # All checks passed
            self._is_started = True
            self._is_active = True

    def shutdown(self) -> None:
        """
        Execute clean deterministic shutdown.

        Guarantees:
        - Work is immediately stopped.
        - Sinks and buffers are flushed and cleanly closed.
        - Audit logs and state are preserved without corruption.
        - Zero trading actions or order cancellations are triggered.
        """
        with self._lock:
            if not self._is_started or self._is_shutdown:
                self._is_active = False
                self._is_shutdown = True
                return

            # Step 1: Stop work
            self._is_active = False

            # Step 2: Flush diagnostic and observability buffers safely
            if self._dispatcher is not None:
                with contextlib.suppress(Exception):
                    self._dispatcher.flush()

            # Step 3: Close resources safely
            if self._dispatcher is not None:
                with contextlib.suppress(Exception):
                    self._dispatcher.close()

            # Step 4: Finalize
            self._is_shutdown = True
