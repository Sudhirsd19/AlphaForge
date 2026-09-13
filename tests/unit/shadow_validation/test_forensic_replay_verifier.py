"""
Unit tests for ForensicReplayVerifier (Phase 17 PS-53, Correction 8).
Tests canonical replay matching and structured failure diff generation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from alphaforge.shadow_validation.enums import DataSourceType
from alphaforge.shadow_validation.forensic_replay_verifier import ForensicReplayVerifier
from alphaforge.shadow_validation.models import ImmutableEvidencePackage


def test_forensic_replay_match() -> None:
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    pkg = ImmutableEvidencePackage.create(
        {
            "run_id": "RUN-1",
            "git_sha": "0060d39",
            "config_hash": "cfg-1",
            "strategy_version": "1.0.0",
            "contract_metadata_version": "3.0.0",
            "data_source_type": DataSourceType.SYNTHETIC,
            "start_time": now,
            "end_time": now,
            "duration_seconds": 10.0,
            "market_sessions_count": 1,
            "valid_events_count": 10,
            "decisions_count": 10,
            "signals_count": 1,
            "orders_count": 1,
            "fills_count": 1,
            "risk_events_count": 0,
            "anomalies": [],
            "pnl_summary": {"net_pnl": "100.00"},
            "reconciliation_result": {"all_aligned": True},
            "canonical_state_hash": "HASH-MATCH-123",
            "replay_result": {},
            "certification_levels": {},
        }
    )

    replayed_state = {"canonical_state_hash": "HASH-MATCH-123"}
    is_match, report = ForensicReplayVerifier.verify_replay(pkg, replayed_state)
    assert is_match is True
    assert report["is_match"] is True
    assert len(report["mismatches"]) == 0


def test_forensic_replay_mismatch_diff() -> None:
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    pkg = ImmutableEvidencePackage.create(
        {
            "run_id": "RUN-2",
            "git_sha": "0060d39",
            "config_hash": "cfg-1",
            "strategy_version": "1.0.0",
            "contract_metadata_version": "3.0.0",
            "data_source_type": DataSourceType.SYNTHETIC,
            "start_time": now,
            "end_time": now,
            "duration_seconds": 10.0,
            "market_sessions_count": 1,
            "valid_events_count": 10,
            "decisions_count": 10,
            "signals_count": 1,
            "orders_count": 1,
            "fills_count": 1,
            "risk_events_count": 0,
            "anomalies": [],
            "pnl_summary": {"net_pnl": "100.00"},
            "reconciliation_result": {"all_aligned": True},
            "canonical_state_hash": "ORIGINAL-HASH-111",
            "replay_result": {},
            "certification_levels": {},
        }
    )

    replayed_corrupted = {"canonical_state_hash": "DIVERGENT-HASH-222"}
    is_match, report = ForensicReplayVerifier.verify_replay(pkg, replayed_corrupted)
    assert is_match is False
    assert len(report["mismatches"]) == 1
    assert report["mismatches"][0]["field"] == "canonical_state_hash"
