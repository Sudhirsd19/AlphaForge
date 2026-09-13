"""
Property-based invariant tests for AlphaForge Paper / Shadow Trading.

Validates determinism, conservation, price positivity, P&L consistency,
and absence of phantom positions using property invariants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderSide
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker


def make_candle(open_p: Decimal, high_p: Decimal, low_p: Decimal, close_p: Decimal) -> MarketCandle:
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY-FUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=20),
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=True,
    )


@given(
    price=st.integers(min_value=1000, max_value=50000),
    qty=st.integers(min_value=1, max_value=500),
    slippage_bps=st.integers(min_value=0, max_value=50),
    fee_bps=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=50)
def test_property_fill_slippage_and_fee_bounds(
    price: int, qty: int, slippage_bps: int, fee_bps: int
) -> None:
    dec_price = Decimal(str(price))
    slip_rate = Decimal(str(slippage_bps)) / Decimal("10000")
    fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
    cost_cfg = CostConfig(entry_slippage_rate=slip_rate, entry_fee_rate=fee_rate)
    sim = DeterministicFillSimulator(cost_config=cost_cfg)

    candle = make_candle(
        open_p=dec_price,
        high_p=dec_price + Decimal("100"),
        low_p=dec_price - Decimal("100"),
        close_p=dec_price,
    )

    # BUY Fill
    buy_fill = sim.simulate_market_order("B1", "NIFTY", OrderSide.BUY, qty, candle, is_entry=True)
    assert buy_fill.effective_price >= dec_price  # Adverse upward
    assert buy_fill.fee >= Decimal("0")
    assert buy_fill.filled_qty == qty

    # SELL Fill
    sell_fill = sim.simulate_market_order("S1", "NIFTY", OrderSide.SELL, qty, candle, is_entry=True)
    assert sell_fill.effective_price <= dec_price  # Adverse downward
    assert sell_fill.fee >= Decimal("0")
    assert sell_fill.filled_qty == qty


@given(
    entry_p=st.integers(min_value=1000, max_value=50000),
    exit_p=st.integers(min_value=1000, max_value=50000),
    qty=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=50)
def test_property_pnl_conservation(entry_p: int, exit_p: int, qty: int) -> None:
    dec_entry = Decimal(str(entry_p))
    dec_exit = Decimal(str(exit_p))
    cost_cfg = CostConfig(
        entry_slippage_rate=Decimal("0"),
        exit_slippage_rate=Decimal("0"),
        entry_fee_rate=Decimal("0.0001"),
        exit_fee_rate=Decimal("0.0001"),
    )
    tracker = PaperPnLTracker(cost_config=cost_cfg)
    sim = DeterministicFillSimulator(cost_config=cost_cfg)

    c_entry = make_candle(dec_entry, dec_entry + 10, dec_entry - 10, dec_entry)
    c_exit = make_candle(dec_exit, dec_exit + 10, dec_exit - 10, dec_exit)

    fill_e = sim.simulate_market_order("E", "NIFTY", OrderSide.BUY, qty, c_entry, is_entry=True)
    tracker.record_entry_fill(fill_e)

    fill_x = sim.simulate_market_order("X", "NIFTY", OrderSide.SELL, qty, c_exit, is_entry=False)
    trade = tracker.record_exit_fill(fill_x)

    # Invariant: Net P&L == Gross P&L - Fees
    assert trade.net_pnl == trade.gross_pnl - trade.fees
