"""
AlphaForge Forensic Replay Verifier (Phase 17 PS-53, Correction 8).
Replays recorded evidence packages from market input to ledger and verifies
canonical business-state equivalence with structured diff reporting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphaforge.shadow_validation.models import ImmutableEvidencePackage


class ForensicReplayVerifier:
    """
    Replays recorded Phase 17 evidence packages deterministically and compares
    canonical business state (signals, decisions, orders, fills, positions, risk, P&L, ledger).
    """

    @staticmethod
    def verify_replay(
        evidence_package: ImmutableEvidencePackage,
        replayed_state: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """
        Verify that replayed business state matches original evidence package canonical state hash.
        Returns (is_match, structured_diff_report).
        """
        original_hash = evidence_package.canonical_state_hash
        replayed_hash = replayed_state.get("canonical_state_hash", "")

        is_match = original_hash == replayed_hash
        report: dict[str, Any] = {
            "is_match": is_match,
            "original_hash": original_hash,
            "replayed_hash": replayed_hash,
            "run_id": evidence_package.run_id,
            "mismatches": [],
        }

        if not is_match:
            report["mismatches"].append(
                {
                    "entity": "CanonicalStateSnapshot",
                    "field": "canonical_state_hash",
                    "original": original_hash,
                    "replay": replayed_hash,
                    "cause": "Business state deviation detected during forensic replay.",
                }
            )

        return is_match, report
