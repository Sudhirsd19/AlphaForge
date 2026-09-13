"""
Unit tests for AlphaForge Shadow Mode Comparator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.models import StrategySignal
from alphaforge.data.enums import DataQualityStatus, InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.paper_shadow.enums import PaperShadowMode, ShadowComparisonOutcome
from alphaforge.paper_shadow.models import PaperShadowConfig
from alphaforge.paper_shadow.shadow_comparator import ShadowComparator
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import RiskDecision


def make_candle(close_p: Decimal = Decimal("20000")) -> MarketCandle:
    t = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY-FUT",
        exchange_timestamp=t,
        received_timestamp=t + timedelta(milliseconds=50),
        timeframe="3m",
        open=close_p,
        high=close_p + Decimal("50"),
        low=close_p - Decimal("50"),
        close=close_p,
        volume=1000,
        source="TEST_FEED",
        quality_status=DataQualityStatus.VALID,
        is_closed=True,
    )


def test_shadow_comparator_records_approved_observation() -> None:
    cfg = PaperShadowConfig(mode=PaperShadowMode.SHADOW)
    comparator = ShadowComparator(config=cfg)
    candle = make_candle(close_p=Decimal("20000"))
    now = datetime(2026, 9, 12, 9, 15, tzinfo=UTC)

    signal = StrategySignal(
        signal_id="SIG-001",
        strategy_id="AF_ORB_V1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        direction=SignalDirection.LONG,
        signal_timestamp=now,
        evaluation_timestamp=now,
        entry_reference=Decimal("20000"),
        stop_reference=Decimal("19950"),
        target_reference=Decimal("20100"),
        risk_distance=Decimal("50"),
        trend_state=TrendState.BULLISH,
        basis_status=FuturesConfirmationStatus.CONFIRMED,
        volume_status="CONFIRMED",
        decision=StrategyDecision.ACCEPT,
        rejection_code=RejectionCode.REJECT_NONE,
        config_hash="HASH",
    )

    risk_dec = RiskDecision(
        decision=RiskDecisionState.APPROVED,
        reason_code=RiskReasonCode.APPROVED,
        reason="Risk within bounds",
        signal_id="SIG-001",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        timestamp=now,
    )

    obs = comparator.record_observation(
        market_event_id="EVT-01",
        symbol="NIFTY",
        candle=candle,
        signal=signal,
        risk_decision=risk_dec,
        evaluation_latency_ns=5000,
    )

    assert obs.comparison_outcome == ShadowComparisonOutcome.MATCH_SIMULATED
    assert obs.intended_quantity == 50
    assert obs.hypothetical_position_qty == 50
    assert obs.hypothetical_fill_price is not None
    assert obs.hypothetical_fill_price >= Decimal("20000")  # Slippage upward on BUY
    assert len(comparator.get_observations()) == 1
