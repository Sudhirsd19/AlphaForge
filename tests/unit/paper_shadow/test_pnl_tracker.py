"""
Unit tests for AlphaForge P&L and Performance Tracker in Paper / Shadow Trading.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alphaforge.backtest.fills import SimulatedFill
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker
from alphaforge.risk.enums import TradeSide


def make_fill(
    order_id: str,
    side: TradeSide,
    qty: int,
    price: Decimal,
    fee: Decimal = Decimal("10"),
    slippage: Decimal = Decimal("5"),
) -> SimulatedFill:
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    notional = price * Decimal(qty)
    return SimulatedFill(
        order_id=order_id,
        timestamp=t,
        symbol="NIFTY",
        side=side,
        intended_qty=qty,
        filled_qty=qty,
        requested_price=price,
        effective_price=price,
        fill_type="MARKET",
        fee=fee,
        slippage_loss=slippage,
        gross_notional=notional,
        reason="TEST_FILL",
    )


def test_pnl_tracker_round_trip_trade() -> None:
    tracker = PaperPnLTracker(initial_capital=Decimal("1000000"))

    # 1. Entry: LONG 50 units @ 20000, fee 15, slippage 10
    entry_fill = make_fill(
        order_id="ORD-ENTRY",
        side=TradeSide.LONG,
        qty=50,
        price=Decimal("20000"),
        fee=Decimal("15"),
        slippage=Decimal("10"),
    )
    tracker.record_entry_fill(entry_fill)
    assert len(tracker.get_active_positions()) == 1

    # 2. Exit: LONG 50 units @ 20100, fee 15, slippage 10
    exit_fill = make_fill(
        order_id="ORD-EXIT",
        side=TradeSide.LONG,
        qty=50,
        price=Decimal("20100"),
        fee=Decimal("15"),
        slippage=Decimal("10"),
    )
    trade = tracker.record_exit_fill(exit_fill, exit_reason="TARGET")

    # Gross P&L: (20100 - 20000) * 50 = 5000
    assert trade.gross_pnl == Decimal("5000")
    # Total fee: 15 (entry) + 15 (exit) = 30
    assert trade.fees == Decimal("30")
    # Net P&L: 5000 - 30 = 4970
    assert trade.net_pnl == Decimal("4970")
    assert len(tracker.get_active_positions()) == 0

    metrics = tracker.get_metrics()
    assert metrics.trade_count == 1
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 0
    assert metrics.win_rate == Decimal("1")
    assert metrics.net_pnl == Decimal("4970")


def test_pnl_tracker_partial_exit() -> None:
    tracker = PaperPnLTracker(initial_capital=Decimal("1000000"))

    entry_fill = make_fill(
        "E1", TradeSide.LONG, 100, Decimal("20000"), Decimal("20"), Decimal("10")
    )
    tracker.record_entry_fill(entry_fill)

    # Partial exit of 40 units
    exit_fill_40 = make_fill(
        "X1", TradeSide.LONG, 40, Decimal("20100"), Decimal("10"), Decimal("5")
    )
    trade1 = tracker.record_exit_fill(exit_fill_40, exit_reason="PARTIAL")

    # Remaining active position should be 60 units
    pos = tracker.get_active_positions().get("NIFTY")
    assert pos is not None
    assert pos.quantity == 60

    # Trade 1 gross P&L: (20100 - 20000) * 40 = 4000
    assert trade1.gross_pnl == Decimal("4000")


def test_pnl_tracker_oversell_error() -> None:
    tracker = PaperPnLTracker()
    entry_fill = make_fill("E1", TradeSide.LONG, 50, Decimal("20000"))
    tracker.record_entry_fill(entry_fill)

    # Attempting to exit 60 units when holding 50 must raise DataIntegrityError
    exit_fill_60 = make_fill("X1", TradeSide.LONG, 60, Decimal("20100"))
    with pytest.raises(DataIntegrityError):
        tracker.record_exit_fill(exit_fill_60)
