"""
Unit tests for Phase 14 Observability Events.

Covers:
- OBS1: Structured event creation with valid taxonomy and deterministic ID.
- OBS2: Event immutability (frozen instance).
- OBS3: JSON serialization round-trip.
- OBS4: Secret redaction in message and nested attributes.
- Attributes type safety and deterministic ID generation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alphaforge.observability.context import TraceContext, trace_span
from alphaforge.observability.events import (
    ObservabilityCategory,
    ObservabilityEvent,
    ObservabilitySeverity,
    OrderEventType,
    StrategyEventType,
    compute_deterministic_event_id,
)
from alphaforge.security.redaction import REDACTION_MASK


def test_obs1_structured_event_creation() -> None:
    """OBS1: Verify structured event creation with explicit taxonomy and deterministic ID."""
    ts = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    event = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_GENERATED.value,
        category=ObservabilityCategory.STRATEGY,
        severity=ObservabilitySeverity.INFO,
        source="alphaforge.strategy",
        symbol="NIFTY",
        mode="PAPER",
        correlation_id="CORR-12345",
        causation_id="CAUS-00001",
        client_order_id="ORD-001",
        message="Signal generated successfully",
        attributes={"entry_price": "24500.50", "quantity": 50, "is_live": False},
        timestamp=ts,
    )

    assert event.event_type == "SIGNAL_GENERATED"
    assert event.category == ObservabilityCategory.STRATEGY
    assert event.severity == ObservabilitySeverity.INFO
    assert event.symbol == "NIFTY"
    assert event.correlation_id == "CORR-12345"
    assert event.causation_id == "CAUS-00001"
    assert event.client_order_id == "ORD-001"
    assert event.message == "Signal generated successfully"
    assert event.attributes["quantity"] == 50
    assert event.event_id.startswith("OBS-")


def test_obs2_event_immutability() -> None:
    """OBS2: Verify that ObservabilityEvent is strictly immutable (frozen)."""
    event = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol="BANKNIFTY",
    )

    with pytest.raises(ValidationError):
        # Pydantic v2 raises ValidationError on mutation attempt of frozen models
        event.symbol = "FINNIFTY"

    with pytest.raises(ValidationError):
        event.attributes = {"mutated": True}


def test_obs3_json_serialization() -> None:
    """OBS3: Verify JSON serialization and round-trip fidelity."""
    ts = datetime(2026, 9, 13, 10, 30, 0, tzinfo=UTC)
    event = ObservabilityEvent(
        event_type=OrderEventType.ORDER_ACK.value,
        category=ObservabilityCategory.ORDER,
        severity=ObservabilitySeverity.INFO,
        symbol="NIFTY",
        client_order_id="CL-999",
        broker_order_id="BR-888",
        attributes={
            "decimal_val": Decimal("123.45"),
            "int_val": 42,
            "nested": {"key": "value", "list": [1, 2, "three"]},
        },
        timestamp=ts,
    )

    json_str = event.to_json()
    assert isinstance(json_str, str)

    parsed = json.loads(json_str)
    assert parsed["event_type"] == "ORDER_ACK"
    assert parsed["category"] == "ORDER"
    assert parsed["symbol"] == "NIFTY"
    assert parsed["client_order_id"] == "CL-999"
    assert parsed["broker_order_id"] == "BR-888"
    assert parsed["attributes"]["decimal_val"] == "123.45"
    assert parsed["attributes"]["nested"]["list"] == [1, 2, "three"]


def test_obs4_secret_redaction_in_message_and_attributes() -> None:
    """OBS4: Verify that sensitive credentials in message or attributes are scrubbed."""
    raw_api_key = "api_key=SECRET_TOKEN_99999"
    raw_password = "password=SuperSecretPassword123"  # noqa: S105

    event = ObservabilityEvent(
        event_type=StrategyEventType.SIGNAL_GENERATED.value,
        category=ObservabilityCategory.STRATEGY,
        message=f"Processing trade with {raw_api_key}",
        attributes={
            "user_credential": raw_password,
            "auth_header": "Authorization: Bearer my_secret_bearer_token",
            "nested": {
                "api_secret": "api_secret: VERY_SECRET_STRING_ABCD",
            },
        },
    )

    # Message must be scrubbed
    assert "SECRET_TOKEN_99999" not in event.message
    assert REDACTION_MASK in event.message

    # Attributes must be scrubbed
    attrs = event.attributes
    assert "SuperSecretPassword123" not in attrs["user_credential"]
    assert REDACTION_MASK in attrs["user_credential"]
    assert "my_secret_bearer_token" not in attrs["auth_header"]
    assert REDACTION_MASK in attrs["auth_header"]
    assert "VERY_SECRET_STRING_ABCD" not in attrs["nested"]["api_secret"]
    assert REDACTION_MASK in attrs["nested"]["api_secret"]

    # Serialization must also be clean
    dumped = event.to_json()
    assert "SECRET_TOKEN_99999" not in dumped
    assert "SuperSecretPassword123" not in dumped
    assert "VERY_SECRET_STRING_ABCD" not in dumped


def test_attributes_type_safety() -> None:
    """Verify that unsupported arbitrary object types in attributes raise TypeError."""

    class CustomObj:
        pass

    with pytest.raises(TypeError, match="Unsupported attribute value of type CustomObj"):
        ObservabilityEvent(
            event_type="TEST",
            category=ObservabilityCategory.SYSTEM,
            attributes={"invalid_obj": CustomObj()},
        )


def test_deterministic_event_id_policy() -> None:
    """Verify that deterministic event identity is stable and distinct."""
    id1 = compute_deterministic_event_id(
        category="STRATEGY",
        event_type="SIGNAL_ACCEPTED",
        source="alphaforge",
        correlation_id="SIG-001",
        symbol="NIFTY",
    )
    id2 = compute_deterministic_event_id(
        category="STRATEGY",
        event_type="SIGNAL_ACCEPTED",
        source="alphaforge",
        correlation_id="SIG-001",
        symbol="NIFTY",
    )
    # Identical domain attributes yield identical event_id
    assert id1 == id2
    assert id1.startswith("OBS-")

    # Distinct correlation yield distinct event_id
    id3 = compute_deterministic_event_id(
        category="STRATEGY",
        event_type="SIGNAL_ACCEPTED",
        source="alphaforge",
        correlation_id="SIG-002",
        symbol="NIFTY",
    )
    assert id1 != id3


def test_id_1_same_seed_and_same_sequence_yields_same_id() -> None:
    """ID-1: Same seed + same sequence -> same event_id."""
    id1 = compute_deterministic_event_id(
        category="ORDER",
        event_type="ORDER_SUBMIT",
        correlation_id="TX-100",
        client_order_id="ORD-01",
        broker_order_id="BRK-01",
        position_id="POS-01",
        symbol="NIFTY",
        sequence=1,
    )
    id2 = compute_deterministic_event_id(
        category="ORDER",
        event_type="ORDER_SUBMIT",
        correlation_id="TX-100",
        client_order_id="ORD-01",
        broker_order_id="BRK-01",
        position_id="POS-01",
        symbol="NIFTY",
        sequence=1,
    )
    assert id1 == id2
    assert id1.startswith("OBS-")


def test_id_2_same_seed_and_different_sequence_yields_different_id() -> None:
    """ID-2: Same seed + different sequence -> different event_id."""
    id1 = compute_deterministic_event_id(
        category="ORDER",
        event_type="ORDER_SUBMIT",
        correlation_id="TX-100",
        client_order_id="ORD-01",
        symbol="NIFTY",
        sequence=1,
    )
    id2 = compute_deterministic_event_id(
        category="ORDER",
        event_type="ORDER_SUBMIT",
        correlation_id="TX-100",
        client_order_id="ORD-01",
        symbol="NIFTY",
        sequence=2,
    )
    assert id1 != id2


def test_id_3_same_symbol_events_in_same_correlation_get_distinct_ordinals_and_ids() -> None:
    """
    ID-3: Two same-symbol events in same correlation chain get distinct ordinals (1, 2)
    producing distinct IDs.
    """
    TraceContext.clear()
    with trace_span("trading_span", correlation_id="CORR-AAPL-100"):
        ev1 = ObservabilityEvent(
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            category=ObservabilityCategory.STRATEGY,
            symbol="AAPL",
        )
        ev2 = ObservabilityEvent(
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            category=ObservabilityCategory.STRATEGY,
            symbol="AAPL",
        )

    assert ev1.correlation_id == "CORR-AAPL-100"
    assert ev2.correlation_id == "CORR-AAPL-100"
    assert ev1.sequence == 1
    assert ev2.sequence == 2
    assert ev1.event_id != ev2.event_id


def test_id_4_concurrent_trades_have_separate_correlation_ids_collision_free() -> None:
    """ID-4: Concurrent trades have separate correlation IDs -> collision-free."""
    TraceContext.clear()
    with trace_span("trade_a", correlation_id="CORR-ALPHA-1"):
        ev_a = ObservabilityEvent(
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            category=ObservabilityCategory.STRATEGY,
            symbol="RELIANCE",
        )
    with trace_span("trade_b", correlation_id="CORR-BETA-2"):
        ev_b = ObservabilityEvent(
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            category=ObservabilityCategory.STRATEGY,
            symbol="RELIANCE",
        )

    assert ev_a.correlation_id != ev_b.correlation_id
    assert ev_a.event_id != ev_b.event_id


def test_id_5_explicitly_passed_event_id_is_preserved() -> None:
    """ID-5: Explicitly passed event_id is preserved."""
    custom_id = "OBS-CUSTOM-PRESERVED-ID"
    ev = ObservabilityEvent(
        event_id=custom_id,
        event_type="TEST_EVENT",
        category=ObservabilityCategory.SYSTEM,
        symbol="INFY",
    )
    assert ev.event_id == custom_id


def test_id_6_serialization_includes_sequence_and_roundtrips_stably() -> None:
    """ID-6: to_dict() / to_json() includes sequence, so serialized events remain stable."""
    ev = ObservabilityEvent(
        event_type=OrderEventType.ORDER_SUBMIT.value,
        category=ObservabilityCategory.ORDER,
        symbol="NIFTY",
        sequence=42,
    )
    d = ev.to_dict()
    assert "sequence" in d
    assert d["sequence"] == 42

    json_str = ev.to_json()
    assert '"sequence":42' in json_str

    deserialized = ObservabilityEvent(**json.loads(json_str))
    assert deserialized.sequence == 42
    assert deserialized.event_id == ev.event_id
    assert deserialized.to_dict() == d
