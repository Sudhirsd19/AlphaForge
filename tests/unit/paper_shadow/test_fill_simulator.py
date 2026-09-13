"""
Unit tests for AlphaForge Deterministic Fill Simulator in Paper / Shadow Trading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.cost.calculator import calculate_effective_price, calculate_fee
from alphaforge.cost.models import CostConfig
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.execution.enums import OrderSide
from alphaforge.paper_shadow.enums import FillType, OHLCResolutionPolicy
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.risk.enums import TradeSide


def make_candle(
    open_p: Decimal = Decimal("20000"),
    high_p: Decimal = Decimal("20100"),
    low_p: Decimal = Decimal("19900"),
    close_p: Decimal = Decimal("20050"),
) -> MarketCandle:
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY-FUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=50),
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


def test_market_buy_slippage_direction() -> None:
    cost_cfg = CostConfig(
        entry_slippage_rate=Decimal("0.0005"),
        entry_fee_rate=Decimal("0.0002"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    candle = make_candle(open_p=Decimal("20000"))

    fill = sim.simulate_market_order(
        order_id="ORD-01",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        candle=candle,
        is_entry=True,
    )

    # BUY entry must slip ADVERSELY UPWARDS: 20000 * (1 + 0.0005) = 20010
    expected_price = Decimal("20000") * (Decimal("1") + Decimal("0.0005"))
    assert fill.effective_price == expected_price
    assert fill.effective_price > fill.requested_price
    assert fill.fill_type == FillType.MARKET.value
    assert fill.side == TradeSide.LONG


def test_market_sell_slippage_direction() -> None:
    cost_cfg = CostConfig(
        entry_slippage_rate=Decimal("0.0005"),
        entry_fee_rate=Decimal("0.0002"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    candle = make_candle(open_p=Decimal("20000"))

    fill = sim.simulate_market_order(
        order_id="ORD-02",
        symbol="NIFTY",
        side=OrderSide.SELL,
        quantity=50,
        candle=candle,
        is_entry=True,
    )

    # SELL entry must slip ADVERSELY DOWNWARDS: 20000 * (1 - 0.0005) = 19990
    expected_price = Decimal("20000") * (Decimal("1") - Decimal("0.0005"))
    assert fill.effective_price == expected_price
    assert fill.effective_price < fill.requested_price
    assert fill.side == TradeSide.SHORT


def test_limit_order_touch_and_fill() -> None:
    cost_cfg = CostConfig(
        entry_slippage_rate=Decimal("0"),
        entry_fee_rate=Decimal("0.0002"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    candle = make_candle(
        open_p=Decimal("20000"),
        high_p=Decimal("20100"),
        low_p=Decimal("19950"),
        close_p=Decimal("20050"),
    )

    # BUY Limit at 19960 (within low 19950) -> should fill
    fill1 = sim.simulate_limit_order(
        order_id="LMT-01",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        limit_price=Decimal("19960"),
        candle=candle,
        is_entry=True,
    )
    assert fill1 is not None
    assert fill1.effective_price == Decimal("19960")

    # BUY Limit at 19940 (below low 19950) -> should NOT fill (None)
    fill2 = sim.simulate_limit_order(
        order_id="LMT-02",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=50,
        limit_price=Decimal("19940"),
        candle=candle,
        is_entry=True,
    )
    assert fill2 is None


def test_conservative_same_bar_sl_tp_ambiguity() -> None:
    cost_cfg = CostConfig(
        exit_slippage_rate=Decimal("0"),
        exit_fee_rate=Decimal("0"),
    )
    sim = DeterministicFillSimulator(
        cost_config=cost_cfg,
        ohlc_policy=OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE,
    )
    # Candle covers both SL (19950) and TP (20050)
    candle = make_candle(
        open_p=Decimal("20000"),
        high_p=Decimal("20100"),
        low_p=Decimal("19900"),
        close_p=Decimal("20020"),
    )

    bracket_res = sim.evaluate_resting_brackets(
        order_id="BRK-01",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        stop_price=Decimal("19950"),
        target_price=Decimal("20050"),
        candle=candle,
    )

    assert bracket_res.triggered is True
    assert bracket_res.bracket_type == "STOP_LOSS"
    assert bracket_res.fill is not None
    assert bracket_res.fill.fill_type == FillType.STOP_LOSS.value
    assert bracket_res.fill.effective_price == Decimal("19950")


def test_phase6_cost_equivalence() -> None:
    cost_cfg = CostConfig(
        entry_fee_rate=Decimal("0.0003"),
        exit_fee_rate=Decimal("0.0003"),
        entry_slippage_rate=Decimal("0.0004"),
        exit_slippage_rate=Decimal("0.0004"),
        fixed_cost_per_trade=Decimal("20"),
    )
    sim = DeterministicFillSimulator(cost_config=cost_cfg)
    candle = make_candle(open_p=Decimal("20000"))

    # Phase 16 execution fill
    fill = sim.simulate_market_order(
        order_id="ORD-EQ",
        symbol="NIFTY",
        side=OrderSide.BUY,
        quantity=100,
        candle=candle,
        is_entry=True,
    )

    # Direct Phase 6 calculation
    p6_effective = calculate_effective_price(
        reference_price=Decimal("20000"),
        slippage_rate=cost_cfg.entry_slippage_rate,
        side=TradeSide.LONG,
        is_entry=True,
    )
    p6_notional = p6_effective * Decimal("100") * Decimal("1")
    p6_fee = calculate_fee(p6_notional, cost_cfg.entry_fee_rate)

    assert fill.effective_price == p6_effective
    assert fill.gross_notional == p6_notional
    assert fill.fee == p6_fee
