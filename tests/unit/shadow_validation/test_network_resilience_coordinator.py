"""
Unit tests for NetworkResilienceCoordinator (Phase 17 PS-45, PS-47).
Tests disconnects, reconnects, duplicate reconnect events, and audit logs.
"""

from __future__ import annotations

from alphaforge.shadow_validation.network_resilience_coordinator import NetworkResilienceCoordinator


def test_network_resilience_lifecycle() -> None:
    coord = NetworkResilienceCoordinator()
    assert coord.is_data_connected is True
    assert coord.is_execution_connected is True

    # Disconnect
    coord.simulate_data_disconnect(reason="Socket timeout")
    assert coord.is_data_connected is False

    coord.simulate_execution_disconnect(reason="Broker simulated failure")
    assert coord.is_execution_connected is False

    # Reconnect
    coord.simulate_data_reconnect(source="NSE_BACKUP_FEED")
    assert coord.is_data_connected is True

    coord.simulate_execution_reconnect()
    assert coord.is_execution_connected is True

    # Duplicate reconnect
    coord.handle_duplicate_reconnect()

    audit = coord.get_resilience_audit()
    assert audit["disconnect_count"] == 2
    assert audit["reconnect_count"] == 3
