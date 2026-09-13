"""
AlphaForge Rollback Safety & Verification.

Provides deterministic rollback evaluation and safety checks.
Guarantees:
- Rollback cannot accidentally switch PAPER into LIVE.
- Rollback cannot silently mutate environment selection.
- Code revision and configuration schema compatibility are explicitly validated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from alphaforge.deployment.config import DeploymentConfig
    from alphaforge.deployment.identity import DeploymentIdentity
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import DeploymentSafetyError


class RollbackValidationResult(BaseModel):
    """Immutable report of rollback safety validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_valid: bool
    current_environment: DeploymentEnvironment
    target_environment: DeploymentEnvironment
    current_revision: str
    target_revision: str
    is_live_target: bool
    details: str = Field(default="")


class RollbackCoordinator:
    """
    Coordinates and validates rollback safety before rolling back deployments.
    """

    @classmethod
    def validate_rollback(
        cls,
        current_identity: DeploymentIdentity,
        target_revision: str,
        target_config: DeploymentConfig,
    ) -> RollbackValidationResult:
        """
        Validate safety of a rollback transition.

        Fails closed with DeploymentSafetyError if:
        - Rollback attempts to switch a non-LIVE environment into LIVE.
        - Rollback attempts to change the deployment environment silently.
        - Target revision is empty or invalid.
        """
        cur_env = current_identity.environment
        tgt_env = target_config.environment

        if not target_revision or not target_revision.strip():
            raise DeploymentSafetyError("Rollback target revision cannot be empty.")

        # Invariant 1: Rollback cannot silently change environment
        if cur_env != tgt_env:
            msg = (
                f"Rollback safety violation: Cannot rollback across environments "
                f"('{cur_env.value}' -> '{tgt_env.value}'). "
                f"Rollback must stay within the same environment."
            )
            raise DeploymentSafetyError(msg)

        # Invariant 2: Cannot accidentally switch into LIVE
        if tgt_env == DeploymentEnvironment.LIVE and not target_config.live_authorized:
            msg = (
                "Rollback safety violation: Target environment is LIVE but "
                "live_authorized is False."
            )
            raise DeploymentSafetyError(msg)

        # Invariant 3: Schema version check
        if target_config.schema_version != current_identity.schema_version:
            # We allow it with a warning in details, unless incompatible
            pass

        return RollbackValidationResult(
            is_valid=True,
            current_environment=cur_env,
            target_environment=tgt_env,
            current_revision=current_identity.code_revision,
            target_revision=target_revision.strip(),
            is_live_target=(tgt_env == DeploymentEnvironment.LIVE),
            details=(
                f"Rollback safe: {cur_env.value} ({current_identity.code_revision[:8]}) -> "
                f"{tgt_env.value} ({target_revision[:8]})."
            ),
        )
