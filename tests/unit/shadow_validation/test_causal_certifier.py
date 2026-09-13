"""
Unit tests for CausalCertifier (Phase 17 PS-49, Correction 1).
Tests anti-lookahead monotonicity (decision_ts >= max(input_ts))
and adversarial future data injection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.risk.enums import TradeSide
from alphaforge.shadow_validation.causal_certifier import CausalCertifier
from alphaforge.shadow_validation.models import ShadowSignalRecord


def test_causal_decision_certification_success() -> None:
    certifier = CausalCertifier()
    t0 = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    decision_ts = t1 + timedelta(seconds=1)

    is_valid = certifier.certify_decision(
        decision_id="DEC-1",
        decision_ts=decision_ts,
        input_event_timestamps=[t0, t1],
    )
    assert is_valid is True
    assert len(certifier.violations) == 0


def test_causal_decision_adversarial_future_injection() -> None:
    certifier = CausalCertifier()
    t0 = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    future_t = datetime(2026, 9, 12, 4, 5, tzinfo=UTC)  # 5 minutes in future
    decision_ts = t0 + timedelta(minutes=1)

    # Ingest future timestamp into earlier decision
    is_valid = certifier.certify_decision(
        decision_id="DEC-ADVERSARIAL-1",
        decision_ts=decision_ts,
        input_event_timestamps=[t0, future_t],
    )
    assert is_valid is False
    assert len(certifier.violations) == 1
    assert "LOOKAHEAD_VIOLATION" in certifier.violations[0]["error"]


def test_certify_signal_record() -> None:
    certifier = CausalCertifier()
    now = datetime(2026, 9, 12, 4, 15, tzinfo=UTC)

    sig = ShadowSignalRecord(
        signal_id="SIG-1",
        decision_id="DEC-1",
        symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        expiry_datetime=now + timedelta(days=12),
        decision_timestamp=now,
        strategy_version="1.0.0",
        strategy_state={"ema_fast": 24500.0},
        market_price=Decimal("24500.00"),
        signal_price=Decimal("24500.00"),
        side=TradeSide.LONG,
        entry_reference=Decimal("24500.00"),
        stop_reference=Decimal("24450.00"),
        tp1=Decimal("24550.00"),
        tp2=Decimal("24600.00"),
        tp3=Decimal("24650.00"),
        risk_state={"account_equity": "1000000.00"},
        portfolio_state={"available_capital": "1000000.00"},
        market_data_version="1.0.0",
        input_event_timestamps=[now - timedelta(minutes=1)],
    )
    assert certifier.certify_signal_record(sig) is True
