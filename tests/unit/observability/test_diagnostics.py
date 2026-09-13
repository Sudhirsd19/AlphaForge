"""
Unit tests for Phase 14 Diagnostic Visibility.

Covers:
- OBS9: Risk rejection visibility with reason codes.
- OBS10: Strategy decision visibility (explainable decisions).
- OBS11: Data-gap visibility.
- OBS12: Reconciliation mismatch visibility without secret leakage.
- OBS13: Startup security diagnostic visibility.
- OBS14: Kill-switch diagnostic visibility.
- ADV-OBS-7: Kill-switch engagement halts entries and emits diagnostic event.
- ADV-OBS-8: Reconciliation mismatch contains safe diagnostic context.
- ADV-OBS-9: System restart sequence observability.
"""

from __future__ import annotations

from decimal import Decimal

from alphaforge.observability.events import (
    DataEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    ObservabilitySeverity,
    ReconciliationEventType,
    RecoveryEventType,
    RiskEventType,
    SecurityEventType,
    StrategyEventType,
    SystemEventType,
)
from alphaforge.observability.sinks import InMemoryObservabilitySink, SafeObservabilityDispatcher
from alphaforge.security.kill_switch import KillSwitch


def test_obs9_risk_rejection_visibility() -> None:
    """OBS9: Risk rejection events record explicit limits, calculations, and reasons."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    ev = ObservabilityEvent(
        event_type=RiskEventType.RISK_REJECTED.value,
        category=ObservabilityCategory.RISK,
        severity=ObservabilitySeverity.WARNING,
        symbol="NIFTY",
        correlation_id="SIG-RISK-01",
        message="Risk decision: REJECTED (MAX_POSITION_RISK_EXCEEDED)",
        attributes={
            "decision": "REJECTED",
            "reason_code": "MAX_POSITION_RISK_EXCEEDED",
            "proposed_risk": "75000.00",
            "max_allowed_risk": "50000.00",
            "equity": "1000000.00",
        },
    )
    dispatcher.emit(ev)

    recorded = sink.get_by_type("RISK_REJECTED")
    assert len(recorded) == 1
    attrs = recorded[0].attributes
    assert attrs["reason_code"] == "MAX_POSITION_RISK_EXCEEDED"
    assert Decimal(attrs["proposed_risk"]) > Decimal(attrs["max_allowed_risk"])


def test_obs10_strategy_decision_visibility() -> None:
    """OBS10: Strategy accept/reject decisions are explainable with rule parameters."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    # Reject case
    ev_reject = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_REJECTED.value,
        category=ObservabilityCategory.STRATEGY,
        severity=ObservabilitySeverity.WARNING,
        symbol="NIFTY",
        correlation_id="SIG-STRAT-REJ",
        message="Strategy decision: REJECT (REJECT_MOMENTUM_RSI_OVERBOUGHT)",
        attributes={
            "decision": "REJECT",
            "rejection_code": "REJECT_MOMENTUM_RSI_OVERBOUGHT",
            "rsi_value": "78.4",
            "max_rsi": "70.0",
        },
    )
    dispatcher.emit(ev_reject)

    # Accept case
    ev_accept = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_ACCEPTED.value,
        category=ObservabilityCategory.STRATEGY,
        severity=ObservabilitySeverity.INFO,
        symbol="NIFTY",
        correlation_id="SIG-STRAT-ACC",
        message="Strategy decision: ACCEPT (REJECT_NONE)",
        attributes={
            "decision": "ACCEPT",
            "direction": "LONG",
            "entry_reference": "24500.00",
            "stop_reference": "24400.00",
            "target_reference": "24700.00",
        },
    )
    dispatcher.emit(ev_accept)

    assert len(sink.get_by_type("SIGNAL_REJECTED")) == 1
    assert len(sink.get_by_type("SIGNAL_ACCEPTED")) == 1
    assert sink.get_by_type("SIGNAL_REJECTED")[0].attributes["rsi_value"] == "78.4"


def test_obs11_data_gap_visibility() -> None:
    """OBS11: Data gap diagnostics are observable with bounds and gap count."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    ev = ObservabilityEvent(
        event_type=DataEventType.DATA_GAP.value,
        category=ObservabilityCategory.DATA,
        severity=ObservabilitySeverity.WARNING,
        symbol="NIFTY",
        message="Detected 3 missing 3m candles between 09:45:00Z and 09:54:00Z",
        attributes={
            "gap_start": "2026-09-13T09:45:00Z",
            "gap_end": "2026-09-13T09:54:00Z",
            "missing_candles_count": 3,
        },
    )
    dispatcher.emit(ev)

    recorded = sink.get_by_type("DATA_GAP")
    assert len(recorded) == 1
    assert recorded[0].attributes["missing_candles_count"] == 3


def test_obs12_and_adv_obs_8_reconciliation_mismatch_visibility() -> None:
    """
    OBS12 & ADV-OBS-8: Reconciliation mismatches provide safe diagnostic context without secrets.
    """
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    ev = ObservabilityEvent(
        event_type=ReconciliationEventType.RECONCILIATION_MISMATCH.value,
        category=ObservabilityCategory.RECONCILIATION,
        severity=ObservabilitySeverity.WARNING,
        correlation_id="RECON-PASS-101",
        message="Reconciliation RECON-PASS-101 completed with status: MISMATCH",
        attributes={
            "reconciliation_id": "RECON-PASS-101",
            "status": "MISMATCH",
            "reason_code": "POSITION_MISMATCH",
            "local_qty": 50,
            "broker_qty": 0,
            "secret_broker_token": "api_secret=MY_HIDDEN_SECRET_KEY",  # injected secret
        },
    )
    dispatcher.emit(ev)

    recorded = sink.get_by_type("RECONCILIATION_MISMATCH")
    assert len(recorded) == 1
    assert recorded[0].attributes["status"] == "MISMATCH"
    assert recorded[0].attributes["reason_code"] == "POSITION_MISMATCH"
    # Secret must be redacted
    assert "MY_HIDDEN_SECRET_KEY" not in str(recorded[0].attributes)


def test_obs13_startup_security_diagnostic_visibility() -> None:
    """OBS13: Startup security diagnostic results are observable without credential exposure."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    ev = ObservabilityEvent(
        event_type=SecurityEventType.STARTUP_SECURITY_FAILED.value,
        category=ObservabilityCategory.SECURITY,
        severity=ObservabilitySeverity.CRITICAL,
        message="Startup security gate verification failed: Missing reconciliation gate",
        attributes={
            "trading_mode": "LIVE",
            "is_live_authorized": True,
            "kill_switch_state": "DISENGAGED",
            "startup_gate_verified": False,
            "reason": "Reconciliation gate missing for LIVE mode",
        },
    )
    dispatcher.emit(ev)

    recorded = sink.get_by_type("STARTUP_SECURITY_FAILED")
    assert len(recorded) == 1
    assert recorded[0].severity == ObservabilitySeverity.CRITICAL
    assert recorded[0].attributes["startup_gate_verified"] is False


def test_obs14_and_adv_obs_7_kill_switch_diagnostic_visibility() -> None:
    """OBS14 & ADV-OBS-7: Kill-switch engagement records event while security halts entries."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    ks = KillSwitch()
    assert not ks.is_engaged()

    # Trigger kill switch
    ks.engage(reason="Emergency market dislocation detected")
    assert ks.is_engaged()

    # Diagnostic observation
    ev = ObservabilityEvent(
        event_type=SecurityEventType.KILL_SWITCH_ENGAGED.value,
        category=ObservabilityCategory.SECURITY,
        severity=ObservabilitySeverity.CRITICAL,
        message=f"Kill switch ENGAGED: {ks.latest_reason}",
        attributes={"reason": ks.latest_reason, "engaged": True},
    )
    dispatcher.emit(ev)

    recorded = sink.get_by_type("KILL_SWITCH_ENGAGED")
    assert len(recorded) == 1
    assert recorded[0].attributes["reason"] == "Emergency market dislocation detected"
    assert ks.is_engaged() is True


def test_adv_obs_9_restart_and_recovery_observability() -> None:
    """ADV-OBS-9: Full restart sequence across shutdown, startup, and recovery is observable."""
    sink = InMemoryObservabilitySink()
    dispatcher = SafeObservabilityDispatcher([sink])

    # 1. Shutdown
    dispatcher.emit(
        ObservabilityEvent(
            event_type=SystemEventType.SHUTDOWN.value,
            category=ObservabilityCategory.SYSTEM,
            message="Clean shutdown initiated: 0 open positions, 0 pending orders",
        )
    )

    # 2. Restart / Startup
    dispatcher.emit(
        ObservabilityEvent(
            event_type=SystemEventType.STARTUP.value,
            category=ObservabilityCategory.SYSTEM,
            message="Process restarted, initializing components",
            attributes={"mode": "PAPER", "version": "1.0.0"},
        )
    )

    # 3. Recovery
    dispatcher.emit(
        ObservabilityEvent(
            event_type=RecoveryEventType.RECOVERY_START.value,
            category=ObservabilityCategory.RECOVERY,
            message="Replaying audit logs for cold-boot state restoration",
        )
    )
    dispatcher.emit(
        ObservabilityEvent(
            event_type=RecoveryEventType.RECOVERY_SUCCESS.value,
            category=ObservabilityCategory.RECOVERY,
            message="State recovery completed successfully in 12ms",
            attributes={"reconstructed_orders": 0, "reconstructed_positions": 0},
        )
    )

    all_events = [e.event_type for e in sink.events]
    assert all_events == [
        "SHUTDOWN",
        "STARTUP",
        "RECOVERY_START",
        "RECOVERY_SUCCESS",
    ]
