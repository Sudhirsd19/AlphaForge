"""
Unit Tests for Phase 18-J Forensic Evidence Package & SHA-256 Manifest.

Verifies:
1. Complete package assembly with builder pattern.
2. Root manifest cryptographic digest calculation.
3. Tamper detection on individual component modification.
4. Tamper detection on root manifest digest tampering.
5. JSON serialization and roundtrip integrity verification.
6. Directory-based export and re-import verification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.shadow_validation.evidence_package import (
    ForensicEvidenceBuilder,
    ForensicEvidencePackage,
)

if TYPE_CHECKING:
    from pathlib import Path


def _create_sample_package() -> ForensicEvidencePackage:
    builder = ForensicEvidenceBuilder(
        package_id="PKG-20260914-001",
        git_commit="7e5f829d7bfd636953e1ad657f1154901a10dc55",
        authoritative_verdict="CONDITIONALLY_ROBUST",
    )
    builder.add_market_events(
        [
            {
                "symbol": "NIFTY26SEPFUT",
                "raw_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "attestation_hmac": (
                    "a1b2c3d4e5f607182930415263748596" "a1b2c3d4e5f607182930415263748596"
                ),
                "price": "24850.50",
            }
        ]
    )
    builder.add_causal_decisions(
        [
            {
                "decision_id": "DEC-001",
                "symbol": "NIFTY26SEPFUT",
                "signal": "LONG",
                "lineage": ["EVT-001"],
            }
        ]
    )
    builder.add_execution_fills(
        [
            {
                "fill_id": "FILL-001",
                "symbol": "NIFTY26SEPFUT",
                "fill_price": "24851.00",
                "slippage": "0.50",
                "fees": "25.00",
            }
        ]
    )
    builder.add_reconciliation_proofs(
        [
            {
                "cycle_id": "RECON-001",
                "trigger": "ON_FILL",
                "kill_switch_tripped": False,
                "discrepancies": [],
            }
        ]
    )
    builder.add_quant_validation_report(
        {
            "sharpe_ratio": "1.85",
            "walk_forward_efficiency": "0.72",
            "verdict": "CONDITIONALLY_ROBUST",
        }
    )
    return builder.build()


def test_evidence_builder_and_manifest_hash() -> None:
    pkg = _create_sample_package()
    assert pkg.manifest.package_id == "PKG-20260914-001"
    assert pkg.manifest.authoritative_verdict == "CONDITIONALLY_ROBUST"
    assert len(pkg.manifest.root_sha256) == 64
    assert len(pkg.manifest.components) == 5

    valid, reason = pkg.verify_integrity()
    assert valid is True
    assert reason == "INTEGRITY_VERIFIED"


def test_evidence_package_tampering_detection_component() -> None:
    pkg = _create_sample_package()

    # Tamper with component data directly (e.g. modify fill price)
    pkg.components["execution_fills"]["fills"][0]["fill_price"] = "24000.00"

    valid, reason = pkg.verify_integrity()
    assert valid is False
    assert "COMPONENT_TAMPERED" in reason
    assert "execution_fills" in reason


def test_evidence_package_tampering_detection_root() -> None:
    pkg = _create_sample_package()

    # Tamper with root manifest hash
    pkg.manifest.root_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

    valid, reason = pkg.verify_integrity()
    assert valid is False
    assert "MANIFEST_TAMPERED" in reason


def test_evidence_package_json_roundtrip() -> None:
    pkg = _create_sample_package()
    json_export = pkg.export_to_json()

    loaded = ForensicEvidencePackage.from_json(json_export)
    assert loaded.manifest.package_id == pkg.manifest.package_id
    assert loaded.manifest.root_sha256 == pkg.manifest.root_sha256

    # Test corrupted JSON rejects
    bad_json = json_export.replace("CONDITIONALLY_ROBUST", "ROBUST")
    with pytest.raises(DataIntegrityError):
        ForensicEvidencePackage.from_json(bad_json)


def test_evidence_package_directory_roundtrip(tmp_path: Path) -> None:
    pkg = _create_sample_package()
    target_dir = tmp_path / "evidence_bundle_001"
    pkg.export_to_directory(target_dir)

    assert (target_dir / "manifest.json").exists()
    assert (target_dir / "market_events.json").exists()
    assert (target_dir / "execution_fills.json").exists()

    loaded = ForensicEvidencePackage.from_directory(target_dir)
    assert loaded.manifest.package_id == pkg.manifest.package_id
    assert loaded.manifest.root_sha256 == pkg.manifest.root_sha256

    # Tamper with a file on disk
    event_file = target_dir / "market_events.json"
    event_file.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        ForensicEvidencePackage.from_directory(target_dir)
