"""
Unit tests for StateReconstructionEngine (Phase 17 PS-46, Correction 2).
Tests canonical business-state normalization and SHA-256 hash comparison
across all 9 lifecycle states.
"""

from __future__ import annotations

from alphaforge.shadow_validation.enums import ProcessLifecycleState
from alphaforge.shadow_validation.state_reconstruction_engine import StateReconstructionEngine


def test_canonical_reconstruction_across_all_9_states() -> None:
    for state in ProcessLifecycleState:
        raw_pre = {
            "symbol": "NIFTY",
            "contract_id": "NIFTY26SEPFUT",
            "expiry_datetime": "2026-09-24T10:00:00Z",
            "lifecycle_state": state.value,
            "position_quantity": 50 if state == ProcessLifecycleState.OPEN_POSITION else 0,
            "average_entry_price": "24500.00",
            "realized_pnl": "1500.00",
            "unrealized_pnl": "0.00",
            "reserved_risk": "5000.00",
            "reserved_notional": "1225000.00",
            "open_trade_count": 1 if state == ProcessLifecycleState.OPEN_POSITION else 0,
            "causation_lineage": ["ORD-1", "ORD-2"],
            "ledger_hash": "HASH-ABC-123",
            # Transient runtime noise
            "process_id": 99912,
            "task_id": "TASK-TRANSIENT-1",
        }

        raw_post = {
            "symbol": "NIFTY",
            "contract_id": "NIFTY26SEPFUT",
            "expiry_datetime": "2026-09-24T10:00:00Z",
            "lifecycle_state": state.value,
            "position_quantity": 50 if state == ProcessLifecycleState.OPEN_POSITION else 0,
            "average_entry_price": "24500.00",
            "realized_pnl": "1500.00",
            "unrealized_pnl": "0.00",
            "reserved_risk": "5000.00",
            "reserved_notional": "1225000.00",
            "open_trade_count": 1 if state == ProcessLifecycleState.OPEN_POSITION else 0,
            "causation_lineage": ["ORD-2", "ORD-1"],  # Sorted by canonicalizer
            "ledger_hash": "HASH-ABC-123",
            # Different transient runtime noise
            "process_id": 11044,
            "task_id": "TASK-NEW-2",
        }

        is_match, pre_h, post_h, diff = StateReconstructionEngine.verify_reconstruction(
            raw_pre, raw_post
        )
        assert is_match is True
        assert pre_h == post_h
        assert len(diff) == 0


def test_reconstruction_detects_business_state_divergence() -> None:
    raw_pre = {
        "symbol": "NIFTY",
        "contract_id": "NIFTY26SEPFUT",
        "position_quantity": 50,
        "average_entry_price": "24500.00",
        "realized_pnl": "0.00",
        "lifecycle_state": "OPEN_POSITION",
    }
    raw_post_corrupted = {
        "symbol": "NIFTY",
        "contract_id": "NIFTY26SEPFUT",
        "position_quantity": 0,  # Corrupted position
        "average_entry_price": "24500.00",
        "realized_pnl": "0.00",
        "lifecycle_state": "OPEN_POSITION",
    }
    is_match, pre_h, post_h, diff = StateReconstructionEngine.verify_reconstruction(
        raw_pre, raw_post_corrupted
    )
    assert is_match is False
    assert pre_h != post_h
    assert "position_quantity" in diff
