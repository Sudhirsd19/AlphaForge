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
