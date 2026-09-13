"""
Unit tests for CertificationReporter (Phase 17 PS-54, Corrections 3, 9).
Tests Level A, B, C evaluation and final verdict determination:
    PHASE 17 PASS | PHASE 17 BLOCKED | PHASE 17 FAIL
"""

from __future__ import annotations

from datetime import UTC, datetime

from alphaforge.shadow_validation.certification_reporter import CertificationReporter
from alphaforge.shadow_validation.enums import (
    CertificationLevelStatus,
    CertificationVerdict,
    DataSourceType,
)
from alphaforge.shadow_validation.models import ImmutableEvidencePackage


def test_certification_verdict_logic() -> None:
    # 1. Level A PASS + Level B PASS + Level C PENDING -> PHASE 17 BLOCKED
    v_blocked = CertificationReporter.evaluate_verdict(
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.PASS,
        level_c=CertificationLevelStatus.PENDING,
    )
    assert v_blocked == CertificationVerdict.PHASE_17_BLOCKED

    # 2. Level A PASS + Level B PASS + Level C PASS -> PHASE 17 PASS
    v_pass = CertificationReporter.evaluate_verdict(
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.PASS,
        level_c=CertificationLevelStatus.PASS,
    )
    assert v_pass == CertificationVerdict.PHASE_17_PASS

    # 3. Any FAIL -> PHASE 17 FAIL
    v_fail = CertificationReporter.evaluate_verdict(
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.FAIL,
        level_c=CertificationLevelStatus.PENDING,
    )
    assert v_fail == CertificationVerdict.PHASE_17_FAIL


def test_generate_certification_report() -> None:
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    pkg = ImmutableEvidencePackage.create(
        {
            "run_id": "RUN-CERT-1",
            "git_sha": "0060d39",
            "config_hash": "cfg-1",
            "strategy_version": "1.0.0",
            "contract_metadata_version": "3.0.0",
            "data_source_type": DataSourceType.SYNTHETIC,
            "start_time": now,
            "end_time": now,
            "duration_seconds": 120.0,
            "market_sessions_count": 1,
            "valid_events_count": 100,
            "decisions_count": 100,
            "signals_count": 2,
            "orders_count": 2,
            "fills_count": 2,
            "risk_events_count": 0,
            "anomalies": [],
            "pnl_summary": {"net_pnl": "250.00"},
            "reconciliation_result": {"all_aligned": True},
            "canonical_state_hash": "HASH-CERT-999",
            "replay_result": {},
            "certification_levels": {},
        }
    )

    report = CertificationReporter.generate_report(
        evidence=pkg,
        level_a=CertificationLevelStatus.PASS,
        level_b=CertificationLevelStatus.PASS,
        level_c=CertificationLevelStatus.PENDING,
    )
    assert report.final_verdict == CertificationVerdict.PHASE_17_BLOCKED
    assert report.level_a_status == CertificationLevelStatus.PASS
    assert report.level_b_status == CertificationLevelStatus.PASS
    assert report.level_c_status == CertificationLevelStatus.PENDING
