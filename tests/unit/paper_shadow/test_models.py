"""
Unit tests for AlphaForge Paper / Shadow Trading Domain Models.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.enums import (
    OHLCResolutionPolicy,
    PaperShadowMode,
)
from alphaforge.paper_shadow.models import (
    ForwardRunReport,
    MarketEvent,
    PaperPerformanceMetrics,
    PaperShadowConfig,
    PaperTradeRecord,
)
from alphaforge.risk.enums import TradeSide


def make_test_candle(
    symbol: str = "NIFTY",
    exchange_ts: datetime | None = None,
    open_p: Decimal = Decimal("20000"),
    high_p: Decimal = Decimal("20100"),
    low_p: Decimal = Decimal("19950"),
    close_p: Decimal = Decimal("20050"),
    is_closed: bool = True,
) -> MarketCandle:
    ts = exchange_ts or datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol=symbol,
        instrument_type=InstrumentType.FUTURES,
        contract_id=f"{symbol}-FUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=50),
        timeframe="3m",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=is_closed,
    )


def test_paper_shadow_config_defaults() -> None:
    config = PaperShadowConfig()
    assert config.mode == PaperShadowMode.PAPER
    assert config.ohlc_policy == OHLCResolutionPolicy.SL_FIRST_CONSERVATIVE
    assert config.contract_multiplier == Decimal("1")
    assert config.lot_size == 1
    assert config.stale_data_threshold_seconds == 15
    assert config.enforce_session_boundaries is True

    # Hash determinism
    h1 = config.compute_config_hash()
    h2 = config.compute_config_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_paper_shadow_config_invalid_multiplier() -> None:
    with pytest.raises((DataIntegrityError, ValidationError)):
        PaperShadowConfig(contract_multiplier=Decimal("-1"))


def test_market_event_causality_validation() -> None:
    candle = make_test_candle()
    mkt_ts = candle.exchange_timestamp
    # Receipt timestamp before market timestamp violates causality
    with pytest.raises((DataIntegrityError, ValidationError)):
        MarketEvent(
            event_id="EVT-01",
            sequence=1,
            candle=candle,
            market_timestamp=mkt_ts,
            receipt_timestamp=mkt_ts - timedelta(seconds=1),
            symbol="NIFTY",
        )


def test_market_event_valid() -> None:
    candle = make_test_candle()
    mkt_ts = candle.exchange_timestamp
    evt = MarketEvent(
        event_id="EVT-01",
        sequence=1,
        candle=candle,
        market_timestamp=mkt_ts,
        receipt_timestamp=mkt_ts + timedelta(milliseconds=10),
        symbol="NIFTY",
    )
    assert evt.event_id == "EVT-01"
    assert evt.sequence == 1
    assert evt.symbol == "NIFTY"


def test_paper_trade_record_immutability() -> None:
    now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    trade = PaperTradeRecord(
        trade_id="TRD-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        entry_order_id="ORD-01",
        exit_order_id="ORD-02",
        entry_timestamp=now,
        exit_timestamp=now + timedelta(minutes=15),
        entry_price=Decimal("20000"),
        exit_price=Decimal("20100"),
        gross_pnl=Decimal("5000"),
        net_pnl=Decimal("4950"),
        fees=Decimal("30"),
        slippage_loss=Decimal("20"),
        is_closed=True,
        exit_reason="TARGET_REACHED",
    )
    assert trade.trade_id == "TRD-001"
    assert trade.gross_pnl == Decimal("5000")
    with pytest.raises((TypeError, ValidationError)):
        trade.gross_pnl = Decimal("6000")  # type: ignore[misc]


def test_forward_run_report_safety_validation() -> None:
    now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    metrics = PaperPerformanceMetrics(
        trade_count=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=Decimal("1"),
        gross_pnl=Decimal("100"),
        net_pnl=Decimal("90"),
        total_fees=Decimal("5"),
        total_slippage=Decimal("5"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        average_trade_pnl=Decimal("90"),
        profit_factor=Decimal("0"),
        current_exposure=Decimal("0"),
        open_positions_count=0,
        realized_pnl=Decimal("90"),
        unrealized_pnl=Decimal("0"),
    )
    # If no_live_orders_submitted is False, model validation must fail
    with pytest.raises((DataIntegrityError, ValidationError)):
        ForwardRunReport(
            run_id="RUN-01",
            mode=PaperShadowMode.PAPER,
            start_time=now,
            end_time=now + timedelta(hours=1),
            total_candles_processed=10,
            total_signals_generated=1,
            total_orders_submitted=1,
            total_fills_executed=1,
            reconciliation_passes=1,
            metrics=metrics,
            configuration_hash="HASH123",
            code_revision="REV123",
            audit_events_count=1,
            observability_events_count=1,
            no_live_orders_submitted=False,
        )
