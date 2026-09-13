"""
AlphaForge Deployment Broker Guard.

Enforces execution safety boundaries at the broker adapter layer.
Validates BOTH selected deployment environment AND underlying broker execution capability
to guarantee paper and shadow runs can NEVER submit orders to a live broker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.paper import PaperBroker

if TYPE_CHECKING:
    from alphaforge.broker.models import (
        BrokerOrder,
        BrokerOrderRequest,
        BrokerPosition,
    )
    from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import DeploymentSafetyError


def unwrap_broker(broker: AbstractBroker) -> AbstractBroker:
    """Unwrap decorator layers (e.g. SecureBroker, DeploymentBrokerGuard) to find core broker."""
    current = broker
    while hasattr(current, "delegate"):
        current = current.delegate
    return current


def is_live_execution_broker(broker: AbstractBroker) -> bool:
    """
    Determine whether a broker adapter has real live execution capability.

    Deterministic capability signals (in priority order):
    1. Underlying PaperBroker instance is strictly simulated (non-live).
    2. Explicit 'is_live_broker' capability boolean on wrapper or core broker.
    3. Heuristic inspection: class names indicating paper/mock/simulated/fake/dummy/test
       environments are strictly non-live.
    4. By default, absent explicit live capability declaration, fail-closed as non-live.
    """
    core = unwrap_broker(broker)

    # 1. PaperBroker is always simulated (strongest domain invariant)
    if isinstance(core, PaperBroker):
        return False

    # 2. Check explicit capability attribute on wrapper or core
    if hasattr(broker, "is_live_broker"):
        val = broker.is_live_broker
        return bool(val() if callable(val) else val)
    if hasattr(core, "is_live_broker"):
        val = core.is_live_broker
        return bool(val() if callable(val) else val)

    # 3. Class name indicators of simulated/mock brokers
    cls_name = type(core).__name__.lower()
    if any(k in cls_name for k in ("paper", "mock", "sim", "fake", "dummy", "test")):
        return False

    # 4. Fail-closed: broker without explicit live capability is not a live broker
    return False


class DeploymentBrokerGuard(AbstractBroker):
    """
    Execution safety boundary around an AbstractBroker.

    Enforces mandatory invariants:
    - PAPER + live broker => REJECT
    - SHADOW + live broker => REJECT
    - PAPER + paper/sim broker => ALLOW
    - SHADOW + shadow/sim broker => ALLOW
    - LIVE + authorized live broker => ALLOW
    - LIVE + unauthorized/malformed security config => REJECT
    """

    def __init__(
        self,
        delegate: AbstractBroker,
        config: DeploymentConfig,
    ) -> None:
        self._delegate = delegate
        self._config = config
        self._is_live = is_live_execution_broker(delegate)

        # Validate pair at construction time (fail-closed)
        self._validate_guard_invariants()

    @property
    def delegate(self) -> AbstractBroker:
        return self._delegate

    @property
    def config(self) -> DeploymentConfig:
        return self._config

    @property
    def is_live_broker(self) -> bool:
        return self._is_live

    def _validate_guard_invariants(self) -> None:
        env = self._config.environment

        # Invariant 1: PAPER + live broker => REJECT
        if env == DeploymentEnvironment.PAPER and self._is_live:
            msg = (
                "Deployment safety violation: PAPER environment cannot be paired "
                "with a live broker. Execution must remain strictly simulated."
            )
            raise DeploymentSafetyError(msg)

        # Invariant 2: SHADOW + live broker => REJECT
        if env == DeploymentEnvironment.SHADOW and self._is_live:
            msg = (
                "Deployment safety violation: SHADOW environment cannot be paired "
                "with a live broker. Shadow execution must remain simulated."
            )
            raise DeploymentSafetyError(msg)

        # Invariant 3: DEV/TEST + live broker => REJECT
        if env in (DeploymentEnvironment.DEV, DeploymentEnvironment.TEST) and self._is_live:
            msg = (
                f"Deployment safety violation: {env.value} environment cannot be paired "
                f"with a live broker."
            )
            raise DeploymentSafetyError(msg)

        # Invariant 4: LIVE validation
        if env == DeploymentEnvironment.LIVE:
            if not self._config.live_authorized:
                msg = (
                    "Deployment safety violation: LIVE environment requires explicit "
                    "live authorization."
                )
                raise DeploymentSafetyError(msg)

            # LIVE execution requires an actual live-capable broker (fail closed)
            core = unwrap_broker(self._delegate)
            if not self._is_live or not is_live_execution_broker(core):
                msg = (
                    f"Deployment safety violation: LIVE environment cannot be paired with a "
                    f"non-live broker ({type(core).__name__}). "
                    f"Execution must use an actual live-capable broker."
                )
                raise DeploymentSafetyError(msg)

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """
        Submit order only after verifying environment and broker safety invariants.
        """
        # Re-validate invariants before every order submission
        self._validate_guard_invariants()

        env = self._config.environment
        if env != DeploymentEnvironment.LIVE and self._is_live:
            msg = (
                f"Order submission rejected: {env.value} environment cannot route "
                f"orders to live broker."
            )
            raise DeploymentSafetyError(msg)

        if env == DeploymentEnvironment.LIVE:
            if not self._config.live_authorized:
                msg = "Order submission rejected: LIVE trading has not been authorized."
                raise DeploymentSafetyError(msg)

            core = unwrap_broker(self._delegate)
            if not self._is_live or not is_live_execution_broker(core):
                msg = (
                    f"Order submission rejected: LIVE environment cannot execute on a "
                    f"non-live broker ({type(core).__name__})."
                )
                raise DeploymentSafetyError(msg)

        return self._delegate.submit_order(request)

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        return self._delegate.get_order(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
        )

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        return self._delegate.get_open_orders()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        return self._delegate.get_positions()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        return self._delegate.cancel_order(client_order_id)

    def is_available(self) -> bool:
        return self._delegate.is_available()
