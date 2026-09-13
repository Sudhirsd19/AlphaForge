"""
Unit tests for RealisticShadowExecutionEngine (Phase 17 PS-43, PS-44).
Tests execution frictions and Cases A through I (partial fills, cancellations, delayed fills,
rapid market movement, partial SL/TP).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.execution.enums import OrderSide
from alphaforge.shadow_validation.enums import FillExecutionType
from alphaforge.shadow_validation.realistic_execution_engine import RealisticShadowExecutionEngine


def test_execution_case_a_immediate_full_fill() -> None:
    engine = RealisticShadowExecutionEngine(
        base_spread=Decimal("0.10"),
        base_slippage_pct=Decimal("0.0002"),
        simulated_latency_ms=10,
    )
    now = datetime(2026, 9, 12, 4, 15, tzinfo=UTC)
    fills = engine.execute_shadow_fill(
        order_id="ORD-100",
        trade_id="TRD-100",
        causation_id="CAUS-100",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.BUY,
        quantity=50,
        reference_price=Decimal("24500.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.IMMEDIATE_FULL,
    )
    assert len(fills) == 1
    f = fills[0]
    assert f.filled_quantity == 50
    assert f.remaining_quantity == 0
    assert f.simulated_fill_price > Decimal("24500.00")
    assert f.latency_ms == 10
    assert f.fees == Decimal("20.00")


def test_execution_case_b_partial_fill_remaining() -> None:
    engine = RealisticShadowExecutionEngine()
    now = datetime(2026, 9, 12, 4, 15, tzinfo=UTC)
    fills = engine.execute_shadow_fill(
        order_id="ORD-101",
        trade_id="TRD-101",
        causation_id="CAUS-101",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=Decimal("24500.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.PARTIAL_REMAINING,
        partial_ratio=Decimal("0.50"),
    )
    assert len(fills) == 2
    f1, f2 = fills[0], fills[1]
    assert f1.filled_quantity == 50
    assert f1.remaining_quantity == 50
    assert f2.filled_quantity == 50
    assert f2.remaining_quantity == 0
    assert f1.filled_quantity + f2.filled_quantity == 100


def test_execution_case_c_partial_cancel() -> None:
    engine = RealisticShadowExecutionEngine()
    now = datetime(2026, 9, 12, 4, 15, tzinfo=UTC)
    fills = engine.execute_shadow_fill(
        order_id="ORD-102",
        trade_id="TRD-102",
        causation_id="CAUS-102",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.BUY,
        quantity=100,
        reference_price=Decimal("24500.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.PARTIAL_CANCEL,
        partial_ratio=Decimal("0.40"),
    )
    assert len(fills) == 1
    f = fills[0]
    assert f.filled_quantity == 40
    assert f.remaining_quantity == 60
    assert f.execution_type == FillExecutionType.PARTIAL_CANCEL


def test_execution_cases_g_h_i_partial_exits() -> None:
    engine = RealisticShadowExecutionEngine()
    now = datetime(2026, 9, 12, 4, 15, tzinfo=UTC)

    # Case G: Partial exit
    fills_g = engine.execute_shadow_fill(
        order_id="ORD-EXIT-1",
        trade_id="TRD-103",
        causation_id="CAUS-103",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.SELL,
        quantity=50,
        reference_price=Decimal("24600.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.PARTIAL_EXIT,
    )
    assert len(fills_g) == 1
    assert fills_g[0].filled_quantity == 25

    # Case H: Partial TP
    fills_h = engine.execute_shadow_fill(
        order_id="ORD-TP-1",
        trade_id="TRD-104",
        causation_id="CAUS-104",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.SELL,
        quantity=50,
        reference_price=Decimal("24700.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.PARTIAL_TP,
    )
    assert len(fills_h) == 1
    assert fills_h[0].execution_type == FillExecutionType.PARTIAL_TP

    # Case I: Partial SL
    fills_i = engine.execute_shadow_fill(
        order_id="ORD-SL-1",
        trade_id="TRD-105",
        causation_id="CAUS-105",
        symbol="NIFTY26SEPFUT",
        side=OrderSide.SELL,
        quantity=50,
        reference_price=Decimal("24400.00"),
        decision_timestamp=now,
        execution_type=FillExecutionType.PARTIAL_SL,
    )
    assert len(fills_i) == 1
    assert fills_i[0].execution_type == FillExecutionType.PARTIAL_SL
