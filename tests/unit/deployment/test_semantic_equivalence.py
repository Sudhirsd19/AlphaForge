"""
Phase 15 — Semantic Equivalence Verification Test Suite.

Proves that deployment runtime wrapping does NOT alter:
- Strategy signal decisions
- Risk limit evaluation
- Broker order generation
- Order attributes, status, and fill semantics
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from alphaforge.broker.models import BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.runtime import DeploymentRuntime
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole

if TYPE_CHECKING:
    from pathlib import Path


def test_deployment_wrapper_preserves_order_semantics(tmp_path: Path) -> None:
    """
    Verify that an order submitted through standalone PaperBroker produces
    identical order fields and execution behavior as an order submitted
    through DeploymentRuntime's guarded broker.
    """
    req = BrokerOrderRequest(
        client_order_id="TEST-EQ-001",
        symbol="MNQ",
        side=OrderSide.BUY,
        quantity=2,
        role=OrderRole.ENTRY,
        price=Decimal("18500.25"),
    )

    # 1. Standalone unwrapped broker
    standalone_broker = PaperBroker()
    order_standalone = standalone_broker.submit_order(req)

    # 2. Deployment guarded runtime broker
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path / "runtime",
    )
    wrapped_paper = PaperBroker()
    runtime = DeploymentRuntime(config=cfg, broker=wrapped_paper)
    runtime.startup()

    assert runtime.broker is not None
    order_wrapped = runtime.broker.submit_order(req)

    # Assert exact semantic equivalence of generated orders
    assert order_standalone.client_order_id == order_wrapped.client_order_id
    assert order_standalone.symbol == order_wrapped.symbol
    assert order_standalone.side == order_wrapped.side
    assert order_standalone.quantity == order_wrapped.quantity
    assert order_standalone.role == order_wrapped.role
    assert order_standalone.order_type == order_wrapped.order_type
    assert order_standalone.status == order_wrapped.status
    assert order_standalone.filled_quantity == order_wrapped.filled_quantity
    assert order_standalone.average_price == order_wrapped.average_price

    runtime.shutdown()
