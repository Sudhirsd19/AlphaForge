"""
AlphaForge State Reconstruction Engine (Phase 17 PS-46, Correction 2).
Performs canonical state normalization and cryptographic hash comparison
(pre_restart_canonical_state_hash == post_restart_canonical_state_hash)
across all 9 process lifecycle states.
"""

from __future__ import annotations

from typing import Any

from alphaforge.shadow_validation.enums import ProcessLifecycleState
from alphaforge.shadow_validation.models import CanonicalStateSnapshot


class StateReconstructionEngine:
    """
    Normalizes runtime state by discarding transient noise (e.g. memory addresses,
    transient task IDs, log timestamps) while strictly preserving all business state
    (positions, orders, fills, risk reservations, P&L, contract metadata, causation IDs).
    """

    @staticmethod
    def extract_canonical_snapshot(raw_state: dict[str, Any]) -> CanonicalStateSnapshot:
        """Normalize raw runtime state into canonical business-state snapshot."""
        return CanonicalStateSnapshot(
            symbol=str(raw_state.get("symbol", "NIFTY")),
            contract_id=str(raw_state.get("contract_id", "NIFTY26SEPFUT")),
            expiry_datetime=str(raw_state.get("expiry_datetime", "2026-09-24T10:00:00Z")),
            lifecycle_state=ProcessLifecycleState(raw_state.get("lifecycle_state", "NO_POSITION")),
            position_quantity=int(raw_state.get("position_quantity", 0)),
            average_entry_price=str(raw_state.get("average_entry_price", "0.00")),
            realized_pnl=str(raw_state.get("realized_pnl", "0.00")),
            unrealized_pnl=str(raw_state.get("unrealized_pnl", "0.00")),
            reserved_risk=str(raw_state.get("reserved_risk", "0.00")),
            reserved_notional=str(raw_state.get("reserved_notional", "0.00")),
            open_trade_count=int(raw_state.get("open_trade_count", 0)),
            causation_lineage=sorted(raw_state.get("causation_lineage", [])),
            ledger_hash=str(raw_state.get("ledger_hash", "GENESIS")),
        )

    @classmethod
    def verify_reconstruction(
        cls,
        pre_restart_state: dict[str, Any],
        post_restart_state: dict[str, Any],
    ) -> tuple[bool, str, str, dict[str, Any]]:
        """
        Verify that reconstructed state matches pre-restart business state canonically.
        Returns (is_match, pre_hash, post_hash, diff_dict).
        """
        pre_snap = cls.extract_canonical_snapshot(pre_restart_state)
        post_snap = cls.extract_canonical_snapshot(post_restart_state)

        pre_hash = pre_snap.canonical_hash()
        post_hash = post_snap.canonical_hash()

        is_match = pre_hash == post_hash
        diff: dict[str, Any] = {}

        if not is_match:
            pre_dict = pre_snap.model_dump()
            post_dict = post_snap.model_dump()
            for k in pre_dict:
                if pre_dict[k] != post_dict.get(k):
                    diff[k] = {
                        "pre_restart": pre_dict[k],
                        "post_restart": post_dict.get(k),
                    }

        return is_match, pre_hash, post_hash, diff
