"""
Unit tests for AlphaForge Order Lifecycle Router in Paper / Shadow Trading.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphaforge.broker.paper import PaperBroker
from alphaforge.core.exceptions import IdempotencyCollisionError
from alphaforge.execution.enums import OrderState
from alphaforge.execution.idempotency import IdempotencyRegistry, OrderIntent, OrderRole
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import PaperShadowConfig
from alphaforge.paper_shadow.order_router import PaperShadowOrderRouter
from alphaforge.risk.enums import TradeSide


def test_paper_order_routing_lifecycle() -> None:
    broker = PaperBroker()
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=cfg, broker=broker)

    # Create & route order
    order, broker_order = router.create_and_route_order(
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-001",
        side=TradeSide.LONG,
        quantity=50,
    )

    assert broker_order is not None
    assert order.state == OrderState.ACKNOWLEDGED
    assert order.quantity == 50
    assert order.filled_quantity == 0

    # Record full fill
    filled_order = router.record_fill_transition(
        client_order_id=order.order_id,
        fill_quantity=50,
        fill_price=Decimal("20000"),
        is_full_fill=True,
    )
    assert filled_order.state == OrderState.FILLED
    assert filled_order.filled_quantity == 50
    assert router.verify_order_quantity_conservation(filled_order) is True


def test_shadow_order_routing_zero_broker_calls() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.SHADOW)
    router = PaperShadowOrderRouter(config=cfg)

    order, broker_order = router.create_and_route_order(
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-002",
        side=TradeSide.SHORT,
        quantity=25,
    )

    # In SHADOW mode, broker_order must be strictly None
    assert broker_order is None
    assert router.guarded_broker is None
    assert order.state == OrderState.ACKNOWLEDGED
    assert order.quantity == 25


def test_order_idempotency_collision_rejected() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    registry = IdempotencyRegistry()
    router = PaperShadowOrderRouter(config=cfg, idempotency_registry=registry)

    # Register initial intent
    order, _ = router.create_and_route_order(
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-SAME",
        side=TradeSide.LONG,
        quantity=50,
    )

    # Conflicting intent registration for the same client_order_id must raise
    # IdempotencyCollisionError
    conflicting_intent = OrderIntent(
        client_order_id=order.order_id,
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-SAME",
        side=TradeSide.SHORT,  # Conflict: SHORT vs LONG
        quantity=50,
    )
    with pytest.raises(IdempotencyCollisionError):
        registry.register(conflicting_intent)
