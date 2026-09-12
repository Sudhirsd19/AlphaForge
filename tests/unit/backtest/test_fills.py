"""
Unit tests for alphaforge.backtest.fills.
Verifies market fills, resting bracket triggers,
and conservative same-bar SL/TP ambiguity resolution.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.backtest.fills import SimulatedFillEngine
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


def _create_candle(
    ts: datetime,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=100,
        source="NSE",
    )


def test_market_entry_fill() -> None:
    """Verify entry fill applies adverse slippage and fees."""
    cfg = CostConfig(
        entry_fee_rate=Decimal("0.0005"),
        entry_slippage_rate=Decimal("0.0002"),
    )
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    c = _create_candle(t0, Decimal("24000"), Decimal("24010"), Decimal("23990"), Decimal("24005"))

    fill = SimulatedFillEngine.simulate_entry(
        order_id="ORD-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        candle=c,
        cost_config=cfg,
    )

    # For LONG: effective_entry = 24000 * (1 + 0.0002) = 24004.80
    assert fill.effective_price == Decimal("24004.80")
    assert fill.fill_type == "MARKET"
    assert fill.fee > Decimal("0")
    assert fill.slippage_loss > Decimal("0")


def test_same_bar_sl_tp_ambiguity_conservative() -> None:
    """
    CRITICAL FORENSIC REQUIREMENT (Section 15):
    When a single candle touches BOTH Stop-Loss and Take-Profit,
    verify that STOP LOSS is triggered first under conservative policy.
    """
    cfg = CostConfig()
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)

    # Entry at 24000. Stop-loss at 23950. Target at 24100.
    # Candle has Low = 23940 (touches SL) and High = 24120 (touches TP)!
    candle_both_touched = _create_candle(
        ts=t0,
        open_p=Decimal("24000"),
        high_p=Decimal("24120"),
        low_p=Decimal("23940"),
        close_p=Decimal("24050"),
    )

    # 1. Conservative policy: MUST choose STOP_LOSS first
    is_hit, fill, reason = SimulatedFillEngine.evaluate_resting_brackets(
        order_id="ORD-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        stop_price=Decimal("23950"),
        target_price=Decimal("24100"),
        candle=candle_both_touched,
        cost_config=cfg,
        conservative_same_bar_sl_first=True,
    )

    assert is_hit is True
    assert reason == "STOP_LOSS"
    assert fill is not None
    assert fill.fill_type == "STOP_LOSS"
    assert fill.requested_price == Decimal("23950")

    # Verify for SHORT side as well
    # Entry at 24000. Stop-loss at 24050. Target at 23900.
    # Candle touches both: High = 24060, Low = 23890
    candle_short_both = _create_candle(
        ts=t0,
        open_p=Decimal("24000"),
        high_p=Decimal("24060"),
        low_p=Decimal("23890"),
        close_p=Decimal("23950"),
    )
    is_hit_s, fill_s, reason_s = SimulatedFillEngine.evaluate_resting_brackets(
        order_id="ORD-002",
        symbol="NIFTY",
        side=TradeSide.SHORT,
        quantity=50,
        stop_price=Decimal("24050"),
        target_price=Decimal("23900"),
        candle=candle_short_both,
        cost_config=cfg,
        conservative_same_bar_sl_first=True,
    )

    assert is_hit_s is True
    assert reason_s == "STOP_LOSS"
    assert fill_s is not None
    assert fill_s.fill_type == "STOP_LOSS"


def test_target_only_fill() -> None:
    """Verify target executes when only target is hit."""
    cfg = CostConfig()
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candle = _create_candle(
        t0, Decimal("24000"), Decimal("24110"), Decimal("23980"), Decimal("24105")
    )

    is_hit, fill, reason = SimulatedFillEngine.evaluate_resting_brackets(
        order_id="ORD-003",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        stop_price=Decimal("23950"),
        target_price=Decimal("24100"),
        candle=candle,
        cost_config=cfg,
    )
    assert is_hit is True
    assert reason == "TARGET"
    assert fill is not None
    assert fill.fill_type == "TAKE_PROFIT"
