"""
AlphaForge Deployment Identity.

Provides deterministic, reproducible deployment identification.
Combines environment, code revision (git SHA), canonical configuration hash,
and schema version into an immutable deployment identity for audit and forensics.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from alphaforge.deployment.config import DeploymentConfig

from alphaforge.deployment.enums import DeploymentEnvironment  # noqa: TC001


def resolve_git_revision(explicit: str | None = None) -> str:
    """
    Resolve active code revision deterministically.

    Order of precedence:
    1. Explicitly provided revision string.
    2. ALPHAFORGE_GIT_COMMIT environment variable.
    3. Git repository rev-parse HEAD.
    4. Fallback 'untracked'.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()

    env_commit = os.environ.get("ALPHAFORGE_GIT_COMMIT", "").strip()
    if env_commit:
        return env_commit

    with contextlib.suppress(Exception):
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit:
                return commit

    return "untracked"


def compute_config_hash(config: DeploymentConfig) -> str:
    """
    Compute a deterministic SHA-256 digest of canonical configuration metadata.
    Uses sorted keys and fixed separators to guarantee identical outputs for identical configs.
    """
    safe_data: dict[str, Any] = config.to_safe_dict()
    canonical_json = json.dumps(safe_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class DeploymentIdentity(BaseModel):
    """Immutable audit record of deployment identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: DeploymentEnvironment
    code_revision: str = Field(description="Git commit SHA or build identifier.")
    config_hash: str = Field(description="SHA-256 digest of canonical configuration.")
    runtime_root: str
    schema_version: str
    composite_id: str

    def to_dict(self) -> dict[str, str]:
        """Return dictionary representation."""
        return {
            "environment": self.environment.value,
            "code_revision": self.code_revision,
            "config_hash": self.config_hash,
            "runtime_root": self.runtime_root,
            "schema_version": self.schema_version,
            "composite_id": self.composite_id,
        }


DeploymentIdentity.model_rebuild()


def get_deployment_identity(
    config: DeploymentConfig,
    git_commit: str | None = None,
) -> DeploymentIdentity:
    """
    Construct a deterministic DeploymentIdentity from DeploymentConfig and code revision.
    """
    code_rev = resolve_git_revision(git_commit)
    cfg_hash = compute_config_hash(config)
    short_rev = code_rev[:8] if len(code_rev) >= 8 else code_rev
    short_hash = cfg_hash[:8]
    comp_id = f"{config.environment.value}_{short_rev}_{short_hash}"

    return DeploymentIdentity(
        environment=config.environment,
        code_revision=code_rev,
        config_hash=cfg_hash,
        runtime_root=str(config.runtime_root),
        schema_version=config.schema_version,
        composite_id=comp_id,
    )
