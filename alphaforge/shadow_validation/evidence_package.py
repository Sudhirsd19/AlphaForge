"""
Forensic Evidence Package & Manifest Generator (Phase 18-J).

Constructs sealed, immutable, tamper-evident forensic bundles containing:
1. Raw market event payloads + SHA-256 wire hashes.
2. Internal attestation HMACs.
3. Normalized records + exchange/received timestamps.
4. Strategy decisions + causal lineage proofs.
5. Execution simulation logs (fills, slippage, latency, fees).
6. Multi-trigger reconciliation proofs and discrepancy audits.
7. Quantitative validation results (Sharpe, WFE, CPCV, Monte Carlo).
8. Root manifest with cryptographic SHA-256 integrity digest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alphaforge.core.exceptions import DataIntegrityError


def compute_sha256(data: bytes | str) -> str:
    """Computes hexadecimal SHA-256 digest of input."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def canonical_json_dumps(obj: Any) -> str:
    """Serializes data structure into sorted, deterministic JSON string."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


class ForensicManifest:
    """Cryptographic root manifest indexing all artifacts in the forensic bundle."""

    def __init__(
        self,
        package_id: str,
        created_at: datetime,
        git_commit: str,
        authoritative_verdict: str,
        components: dict[str, str],  # component_name -> sha256_hash
        root_sha256: str | None = None,
    ) -> None:
        self.package_id = package_id
        self.created_at = created_at
        self.git_commit = git_commit
        self.authoritative_verdict = authoritative_verdict
        self.components = dict(sorted(components.items()))
        self.root_sha256 = root_sha256 or self.compute_root_hash()

    def compute_root_hash(self) -> str:
        """Computes root SHA-256 digest over sorted component digests and metadata."""
        canonical_content = canonical_json_dumps(
            {
                "authoritative_verdict": self.authoritative_verdict,
                "components": self.components,
                "created_at": self.created_at.isoformat(),
                "git_commit": self.git_commit,
                "package_id": self.package_id,
            }
        )
        return compute_sha256(canonical_content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": "1.0.0",
            "package_id": self.package_id,
            "created_at": self.created_at.isoformat(),
            "git_commit": self.git_commit,
            "authoritative_verdict": self.authoritative_verdict,
            "components": self.components,
            "root_sha256": self.root_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForensicManifest:
        created_at_dt = datetime.fromisoformat(data["created_at"])
        return cls(
            package_id=data["package_id"],
            created_at=created_at_dt,
            git_commit=data["git_commit"],
            authoritative_verdict=data["authoritative_verdict"],
            components=data["components"],
            root_sha256=data.get("root_sha256"),
        )


class ForensicEvidencePackage:
    """Sealed bundle containing all verified forensic artifacts and root manifest."""

    def __init__(
        self,
        manifest: ForensicManifest,
        components: dict[str, dict[str, Any]],
    ) -> None:
        self.manifest = manifest
        self.components = components

    def get_component(self, name: str) -> dict[str, Any]:
        if name not in self.components:
            raise KeyError(f"Component '{name}' not found in evidence package")
        return self.components[name]

    def verify_integrity(self) -> tuple[bool, str]:
        """
        Verifies complete cryptographic integrity:
        1. Validates root manifest hash.
        2. Validates every component matches its registered SHA-256 digest.
        """
        # 1. Verify root manifest digest
        expected_root = self.manifest.compute_root_hash()
        if self.manifest.root_sha256 != expected_root:
            return False, (
                f"MANIFEST_TAMPERED: Root digest mismatch! "
                f"Recorded={self.manifest.root_sha256}, Computed={expected_root}"
            )

        # 2. Verify individual components
        for comp_name, expected_hash in self.manifest.components.items():
            if comp_name not in self.components:
                return False, f"MISSING_COMPONENT: '{comp_name}' listed in manifest but missing"

            content_json = canonical_json_dumps(self.components[comp_name])
            actual_hash = compute_sha256(content_json)
            if actual_hash != expected_hash:
                return False, (
                    f"COMPONENT_TAMPERED: Component '{comp_name}' hash mismatch! "
                    f"Recorded={expected_hash}, Computed={actual_hash}"
                )

        return True, "INTEGRITY_VERIFIED"

    def export_to_json(self) -> str:
        """Exports entire sealed bundle to a single JSON string."""
        bundle = {
            "manifest": self.manifest.to_dict(),
            "components": self.components,
        }
        return canonical_json_dumps(bundle)

    def export_to_directory(self, target_dir: Path | str) -> Path:
        """Exports bundle to disk with separate component files and manifest.json."""
        out_dir = Path(target_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        for comp_name, comp_data in self.components.items():
            comp_path = out_dir / f"{comp_name}.json"
            comp_path.write_text(canonical_json_dumps(comp_data), encoding="utf-8")

        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(canonical_json_dumps(self.manifest.to_dict()), encoding="utf-8")
        return out_dir

    @classmethod
    def from_json(cls, json_str: str) -> ForensicEvidencePackage:
        data = json.loads(json_str)
        manifest = ForensicManifest.from_dict(data["manifest"])
        components = data["components"]
        package = cls(manifest=manifest, components=components)
        valid, reason = package.verify_integrity()
        if not valid:
            raise DataIntegrityError(f"Corrupted evidence package: {reason}")
        return package

    @classmethod
    def from_directory(cls, dir_path: Path | str) -> ForensicEvidencePackage:
        in_dir = Path(dir_path)
        manifest_path = in_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found in {in_dir}")

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ForensicManifest.from_dict(manifest_data)

        components: dict[str, dict[str, Any]] = {}
        for comp_name in manifest.components:
            comp_path = in_dir / f"{comp_name}.json"
            if not comp_path.exists():
                raise FileNotFoundError(f"Component file {comp_path} not found")
            components[comp_name] = json.loads(comp_path.read_text(encoding="utf-8"))

        package = cls(manifest=manifest, components=components)
        valid, reason = package.verify_integrity()
        if not valid:
            raise DataIntegrityError(f"Corrupted evidence package directory: {reason}")
        return package


class ForensicEvidenceBuilder:
    """Builder for assembling and sealing a forensic evidence package."""

    def __init__(
        self,
        package_id: str,
        git_commit: str = "UNKNOWN",
        authoritative_verdict: str = "INSUFFICIENT_DATA",
    ) -> None:
        self.package_id = package_id
        self.git_commit = git_commit
        self.authoritative_verdict = authoritative_verdict
        self._components: dict[str, dict[str, Any]] = {}

    def set_verdict(self, verdict: str) -> ForensicEvidenceBuilder:
        self.authoritative_verdict = verdict
        return self

    def add_component(self, name: str, data: dict[str, Any]) -> ForensicEvidenceBuilder:
        self._components[name] = data
        return self

    def add_market_events(self, events: list[dict[str, Any]]) -> ForensicEvidenceBuilder:
        """Adds raw events, payload hashes, and AlphaForge internal attestation HMACs."""
        return self.add_component(
            "market_events",
            {
                "event_count": len(events),
                "events": events,
            },
        )

    def add_causal_decisions(self, decisions: list[dict[str, Any]]) -> ForensicEvidenceBuilder:
        """Adds decision records with lineage linking input events to outputs."""
        return self.add_component(
            "causal_decisions",
            {
                "decision_count": len(decisions),
                "decisions": decisions,
            },
        )

    def add_execution_fills(self, fills: list[dict[str, Any]]) -> ForensicEvidenceBuilder:
        """Adds simulated realistic execution fills with slippage, latency, fees."""
        return self.add_component(
            "execution_fills",
            {
                "fill_count": len(fills),
                "fills": fills,
            },
        )

    def add_reconciliation_proofs(self, cycles: list[dict[str, Any]]) -> ForensicEvidenceBuilder:
        """Adds multi-trigger reconciliation audit cycles and discrepancy records."""
        return self.add_component(
            "reconciliation_proofs",
            {
                "cycle_count": len(cycles),
                "cycles": cycles,
            },
        )

    def add_quant_validation_report(self, report: dict[str, Any]) -> ForensicEvidenceBuilder:
        """Adds quantitative validation metrics, WFE, CPCV, and sensitivity analysis."""
        return self.add_component("quant_validation_report", report)

    def add_risk_audit(self, risk_events: list[dict[str, Any]]) -> ForensicEvidenceBuilder:
        """Adds risk guardrail evaluations and drawdown governor events."""
        return self.add_component(
            "risk_audit",
            {
                "risk_event_count": len(risk_events),
                "risk_events": risk_events,
            },
        )

    def build(self) -> ForensicEvidencePackage:
        """Seals all components, computes cryptographic hashes, and creates the manifest."""
        component_hashes: dict[str, str] = {}
        for name, data in self._components.items():
            canon = canonical_json_dumps(data)
            component_hashes[name] = compute_sha256(canon)

        manifest = ForensicManifest(
            package_id=self.package_id,
            created_at=datetime.now(UTC),
            git_commit=self.git_commit,
            authoritative_verdict=self.authoritative_verdict,
            components=component_hashes,
        )

        return ForensicEvidencePackage(
            manifest=manifest,
            components=dict(self._components),
        )
