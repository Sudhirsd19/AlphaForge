"""
Unit tests for Trailing Stop / Breakeven at +1R (Upgrade 2).
Verifies that active positions achieving +1R unrealized profit have their stop-loss
trailed to entry_price (Cost-to-Cost / Breakeven), protecting capital from sudden reversals.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.fills import SimulatedFill
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderSide
from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import PaperShadowConfig
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker
from alphaforge.risk.enums import TradeSide


def make_test_candle(
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    ts: datetime | None = None,
) -> MarketCandle:
    t = ts or datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26OCTFUT",
        exchange_timestamp=t,
        received_timestamp=t,
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=5000,
        source="TEST",
        quality_status=DataQualityStatus.VALID,
        is_closed=True,
    )


def test_long_trailing_stop_to_breakeven() -> None:
    tracker = PaperPnLTracker(initial_capital=Decimal("1000000"))
    entry_fill = SimulatedFill(
        order_id="ORD-1",
        timestamp=datetime(2026, 9, 23, 10, 0, tzinfo=UTC),
        symbol="NIFTY",
        side=TradeSide.LONG,
        intended_qty=25,
        filled_qty=25,
        requested_price=Decimal("23400.00"),
        effective_price=Decimal("23400.00"),
        gross_notional=Decimal("23400.00") * Decimal(25),
        fee=Decimal("20.00"),
        slippage_loss=Decimal("0.00"),
        fill_type="FULL",
        reason="ENTRY_MARKET_OPEN",
    )
    # Entry 23400, Stop 23350 (Risk = 50 pts), Target 23500 (+2R = 100 pts)
    tracker.record_entry_fill(
        fill=entry_fill,
        stop_price=Decimal("23350.00"),
        target_price=Decimal("23500.00"),
    )

    pos = tracker.get_active_positions()["NIFTY"]
    assert pos.stop_price == Decimal("23350.00")
    assert pos.is_breakeven_trailed is False

    # Candle 1: High 23440 (only +40 pts < +1R). Should NOT trail.
    c1 = make_test_candle(Decimal("23400"), Decimal("23440"), Decimal("23380"), Decimal("23420"))
    trailed, _, _ = tracker.update_trailing_stop_to_breakeven("NIFTY", c1)
    assert trailed is False
    assert tracker.get_active_positions()["NIFTY"].stop_price == Decimal("23350.00")

    # Candle 2: High 23455 (achieved +55 pts >= +1R of 50 pts). MUST trail to 23400.00!
    c2 = make_test_candle(Decimal("23420"), Decimal("23455"), Decimal("23410"), Decimal("23450"))
    trailed, old_stop, new_stop = tracker.update_trailing_stop_to_breakeven("NIFTY", c2)
    assert trailed is True
    assert old_stop == Decimal("23350.00")
    assert new_stop == Decimal("23400.00")

    pos_updated = tracker.get_active_positions()["NIFTY"]
    assert pos_updated.stop_price == Decimal("23400.00")
    assert pos_updated.is_breakeven_trailed is True


def test_short_trailing_stop_to_breakeven() -> None:
    tracker = PaperPnLTracker(initial_capital=Decimal("1000000"))
    entry_fill = SimulatedFill(
        order_id="ORD-2",
        timestamp=datetime(2026, 9, 23, 10, 0, tzinfo=UTC),
        symbol="NIFTY",
        side=TradeSide.SHORT,
        intended_qty=25,
        filled_qty=25,
        requested_price=Decimal("23400.00"),
        effective_price=Decimal("23400.00"),
        gross_notional=Decimal("23400.00") * Decimal(25),
        fee=Decimal("20.00"),
        slippage_loss=Decimal("0.00"),
        fill_type="FULL",
        reason="ENTRY_MARKET_OPEN",
    )
    # Entry 23400, Stop 23450 (Risk = 50 pts), Target 23300 (+2R = 100 pts)
    tracker.record_entry_fill(
        fill=entry_fill,
        stop_price=Decimal("23450.00"),
        target_price=Decimal("23300.00"),
    )

    pos = tracker.get_active_positions()["NIFTY"]
    assert pos.stop_price == Decimal("23450.00")

    # Candle 1: Low 23360 (only -40 pts < +1R). Should NOT trail.
    c1 = make_test_candle(Decimal("23400"), Decimal("23410"), Decimal("23360"), Decimal("23380"))
    trailed, _, _ = tracker.update_trailing_stop_to_breakeven("NIFTY", c1)
    assert trailed is False

    # Candle 2: Low 23340 (achieved -60 pts >= +1R of 50 pts). MUST trail to 23400.00!
    c2 = make_test_candle(Decimal("23380"), Decimal("23390"), Decimal("23340"), Decimal("23350"))
    trailed, old_stop, new_stop = tracker.update_trailing_stop_to_breakeven("NIFTY", c2)
    assert trailed is True
    assert old_stop == Decimal("23450.00")
    assert new_stop == Decimal("23400.00")
    assert tracker.get_active_positions()["NIFTY"].stop_price == Decimal("23400.00")


def test_engine_bracket_evaluation_with_trailing_stop() -> None:
    """Verify that PaperShadowEngine trails stop to breakeven when processing candles."""
    from alphaforge.broker.models import BrokerOrderRequest, BrokerOrderType
    from alphaforge.execution.idempotency import OrderRole

    engine = PaperShadowEngine(config=PaperShadowConfig(mode=PaperShadowMode.PAPER))

    # Submit and fill entry in raw broker
    req = BrokerOrderRequest(
        client_order_id="ORD-ENG-1",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=25,
        role=OrderRole.ENTRY,
        order_type=BrokerOrderType.MARKET,
    )
    engine._raw_broker.submit_order(req)
    engine._raw_broker.simulate_full_fill("ORD-ENG-1", Decimal("23400.00"))

    # Manually register an active LONG position in engine's pnl tracker
    entry_fill = SimulatedFill(
        order_id="ORD-ENG-1",
        timestamp=datetime(2026, 9, 23, 10, 0, tzinfo=UTC),
        symbol="NIFTY",
        side=TradeSide.LONG,
        intended_qty=25,
        filled_qty=25,
        requested_price=Decimal("23400.00"),
        effective_price=Decimal("23400.00"),
        gross_notional=Decimal("23400.00") * Decimal(25),
        fee=Decimal("20.00"),
        slippage_loss=Decimal("0.00"),
        fill_type="FULL",
        reason="ENTRY_MARKET_OPEN",
    )
    engine._pnl_tracker.record_entry_fill(
        fill=entry_fill,
        stop_price=Decimal("23350.00"),
        target_price=Decimal("23500.00"),
    )

    # 1. Candle where high reaches 23460 (+60 pts > 50 pts risk). Trailing stop should move to 23400!
    c_trail = make_test_candle(
        open_p=Decimal("23420"),
        high_p=Decimal("23460"),
        low_p=Decimal("23410"),
        close_p=Decimal("23450"),
        ts=datetime(2026, 9, 23, 10, 3, tzinfo=UTC),
    )
    engine._evaluate_active_position_brackets("NIFTY", c_trail)
    pos = engine._pnl_tracker.get_active_positions().get("NIFTY")
    assert pos is not None
    assert pos.stop_price == Decimal("23400.00")
    assert pos.is_breakeven_trailed is True

    # 2. Candle reverses down to 23390 (below entry 23400, but above old stop 23350).
    # Since stop is trailed to 23400, bracket triggers exit at 23400 (Breakeven)!
    c_exit = make_test_candle(
        open_p=Decimal("23430"),
        high_p=Decimal("23430"),
        low_p=Decimal("23390"),
        close_p=Decimal("23395"),
        ts=datetime(2026, 9, 23, 10, 6, tzinfo=UTC),
    )
    engine._evaluate_active_position_brackets("NIFTY", c_exit)

    # Position should now be CLOSED!
    assert "NIFTY" not in engine._pnl_tracker.get_active_positions()
    trades = engine._pnl_tracker.get_closed_trades()
    assert len(trades) == 1
    # Exit price should be breakeven (23400) and gross_pnl exactly 0!
    assert trades[0].exit_price == Decimal("23400.00")
    assert trades[0].gross_pnl == Decimal("0.00")

