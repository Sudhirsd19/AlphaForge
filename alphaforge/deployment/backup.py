"""
AlphaForge Deterministic Backup & Safe Recovery.

Provides bit-for-bit reproducible backup creation and strict recovery boundaries.
Guarantees:
- Genuinely deterministic ZIP archives (sorted paths, normalized timestamps, canonical manifest).
- Repeated runs over identical files produce identical SHA-256 archive hashes.
- Zero crossover into LIVE: generic restore cannot target LIVE, and non-LIVE backups can
  NEVER be restored into LIVE under any circumstances (by default or by force).
- Dedicated controlled recovery procedure for LIVE disaster recovery.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import DeploymentSafetyError
from alphaforge.deployment.isolation import EnvironmentDirectoryManager

NORMALIZED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
MANIFEST_FILENAME = "backup_manifest.json"
LIVE_RECOVERY_CONFIRMATION_PHRASE = "CONFIRM_RESTORE_LIVE_RECOVERY"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class DeterministicBackupManager:
    """
    Manages deterministic backup archiving and recovery for personal trading deployments.
    """

    @classmethod
    def create_backup(
        cls,
        config: DeploymentConfig,
        output_path: Path | None = None,
    ) -> tuple[Path, str]:
        """
        Create a bit-for-bit deterministic ZIP archive of state, audit, and sanitized config.

        Returns (archive_path, archive_sha256).
        """
        env = config.environment
        env_str = env.value.lower()

        if output_path is None:
            backup_dir = config.runtime_root / "backups" / env_str
            backup_dir.mkdir(parents=True, exist_ok=True)
            output_path = backup_dir / f"{env_str}_backup.zip"
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # Collect files to include from state and audit directories
        state_dir = config.effective_state_dir
        audit_dir = config.effective_audit_dir

        collected_files: list[tuple[str, Path]] = []
        if state_dir.exists():
            for p in sorted(state_dir.rglob("*")):
                if p.is_file():
                    rel = f"state/{p.relative_to(state_dir).as_posix()}"
                    collected_files.append((rel, p))

        if audit_dir.exists():
            for p in sorted(audit_dir.rglob("*")):
                if p.is_file():
                    rel = f"audit/{p.relative_to(audit_dir).as_posix()}"
                    collected_files.append((rel, p))

        # Sort files deterministically by relative path
        collected_files.sort(key=lambda x: x[0])

        # Build deterministic manifest
        file_entries: list[dict[str, Any]] = []
        for rel, p in collected_files:
            file_entries.append(
                {
                    "path": rel,
                    "sha256": compute_file_sha256(p),
                    "size_bytes": p.stat().st_size,
                }
            )

        manifest_data: dict[str, Any] = {
            "environment": env.value,
            "schema_version": config.schema_version,
            "file_count": len(file_entries),
            "files": file_entries,
            "config": config.to_safe_dict(),
        }
        canonical_manifest_bytes = json.dumps(
            manifest_data,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")

        # Write to temporary in-memory or staging file with normalized zip metadata
        # Using ZIP_STORED guarantees no compression differences across zlib builds
        staging_path = output_path.with_suffix(".tmp")
        with zipfile.ZipFile(staging_path, "w", compression=zipfile.ZIP_STORED) as zf:
            # 1. Write manifest first
            manifest_info = zipfile.ZipInfo(MANIFEST_FILENAME, date_time=NORMALIZED_ZIP_TIMESTAMP)
            manifest_info.external_attr = 0o644 << 16
            zf.writestr(manifest_info, canonical_manifest_bytes)

            # 2. Write collected files in sorted order
            for rel, p in collected_files:
                zinfo = zipfile.ZipInfo(rel, date_time=NORMALIZED_ZIP_TIMESTAMP)
                zinfo.external_attr = 0o644 << 16
                content = p.read_bytes()
                zf.writestr(zinfo, content)

        # Atomic rename to final output
        staging_path.replace(output_path)
        archive_hash = compute_file_sha256(output_path)
        return output_path, archive_hash

    @classmethod
    def verify_backup(cls, archive_path: Path) -> tuple[bool, str]:
        """
        Verify the integrity of a backup archive against its internal manifest.
        """
        if not archive_path.exists():
            return False, f"Archive path does not exist: {archive_path}"

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                if MANIFEST_FILENAME not in zf.namelist():
                    return False, f"Missing manifest '{MANIFEST_FILENAME}' in archive."

                manifest_bytes = zf.read(MANIFEST_FILENAME)
                manifest_data = json.loads(manifest_bytes.decode("utf-8"))
                expected_files = manifest_data.get("files", [])

                for item in expected_files:
                    path_str = item["path"]
                    expected_sha = item["sha256"]
                    if path_str not in zf.namelist():
                        return False, f"Archive missing file recorded in manifest: {path_str}"
                    data = zf.read(path_str)
                    actual_sha = hashlib.sha256(data).hexdigest()
                    if actual_sha != expected_sha:
                        msg = (
                            f"Checksum mismatch for '{path_str}': "
                            f"expected {expected_sha}, got {actual_sha}"
                        )
                        return False, msg

            return True, "Archive verified successfully."
        except Exception as exc:
            return False, f"Archive verification failed with error: {exc}"

    @classmethod
    def restore_backup(
        cls,
        archive_path: Path,
        target_config: DeploymentConfig,
    ) -> bool:
        """
        Restore a backup into target environment runtime directories.

        Safety Invariants:
        - Restore into LIVE is strictly prohibited through generic restore.
        - Cross-environment restore is prohibited (e.g. PAPER into SHADOW).
        - Archive integrity is verified prior to unpacking.
        """
        target_env = target_config.environment

        # Strict Rule 5: Generic restore into LIVE is completely forbidden
        if target_env == DeploymentEnvironment.LIVE:
            msg = (
                "Generic restore into LIVE is disabled for safety. "
                "LIVE recovery requires the dedicated controlled procedure "
                "'restore_live_recovery()'."
            )
            raise DeploymentSafetyError(msg)

        # Verify archive integrity
        valid, msg = cls.verify_backup(archive_path)
        if not valid:
            raise DeploymentSafetyError(f"Cannot restore invalid backup: {msg}")

        # Check manifest environment
        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest_bytes = zf.read(MANIFEST_FILENAME)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            archive_env_str = manifest.get("environment")

        if archive_env_str != target_env.value:
            err = (
                f"Cross-environment restore violation: backup environment is '{archive_env_str}', "
                f"but target environment is '{target_env.value}'. "
                f"Cross-environment restore is forbidden."
            )
            raise DeploymentSafetyError(err)

        # Initialize directories with marker validation
        EnvironmentDirectoryManager.initialize_directories(target_config)

        # Unpack files into target directories
        state_dir = target_config.effective_state_dir
        audit_dir = target_config.effective_audit_dir

        with zipfile.ZipFile(archive_path, "r") as zf:
            for item in manifest.get("files", []):
                rel_path = item["path"]
                content = zf.read(rel_path)
                if rel_path.startswith("state/"):
                    sub = rel_path[len("state/") :]
                    dest = state_dir / sub
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
                elif rel_path.startswith("audit/"):
                    sub = rel_path[len("audit/") :]
                    dest = audit_dir / sub
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)

        return True

    @classmethod
    def restore_live_recovery(
        cls,
        archive_path: Path,
        target_config: DeploymentConfig,
        confirmation_phrase: str,
    ) -> bool:
        """
        Controlled recovery procedure for LIVE environment disaster recovery.

        Requirements:
        - Target must be LIVE and live_authorized must be True.
        - confirmation_phrase must strictly match LIVE_RECOVERY_CONFIRMATION_PHRASE.
        - Backup archive MUST have originated from LIVE (no crossover into LIVE is ever permitted).
        """
        if target_config.environment != DeploymentEnvironment.LIVE:
            raise DeploymentSafetyError("restore_live_recovery can only target LIVE environment.")

        if not target_config.live_authorized:
            raise DeploymentSafetyError("restore_live_recovery requires live_authorized=True.")

        if confirmation_phrase != LIVE_RECOVERY_CONFIRMATION_PHRASE:
            raise DeploymentSafetyError("Invalid confirmation phrase for live recovery.")

        # Verify archive
        valid, msg = cls.verify_backup(archive_path)
        if not valid:
            raise DeploymentSafetyError(f"Cannot restore invalid backup: {msg}")

        # Check archive origin environment
        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest_bytes = zf.read(MANIFEST_FILENAME)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            archive_env_str = manifest.get("environment")

        # Invariant: Non-LIVE backups can NEVER be restored into LIVE under ANY circumstances!
        if archive_env_str != DeploymentEnvironment.LIVE.value:
            msg = (
                f"Severe safety violation: Attempted to restore '{archive_env_str}' "
                f"backup into LIVE. Crossover into LIVE is prohibited by default and by force."
            )
            raise DeploymentSafetyError(msg)

        EnvironmentDirectoryManager.initialize_directories(target_config)
        state_dir = target_config.effective_state_dir
        audit_dir = target_config.effective_audit_dir

        with zipfile.ZipFile(archive_path, "r") as zf:
            for item in manifest.get("files", []):
                rel_path = item["path"]
                content = zf.read(rel_path)
                if rel_path.startswith("state/"):
                    dest = state_dir / rel_path[len("state/") :]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
                elif rel_path.startswith("audit/"):
                    dest = audit_dir / rel_path[len("audit/") :]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)

        return True
