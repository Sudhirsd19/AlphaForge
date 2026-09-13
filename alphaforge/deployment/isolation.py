"""
AlphaForge Environment Isolation & Directory Management.

Enforces physical and logical partitioning of runtime state, logs, audit trails,
and observability artifacts across DEV, TEST, PAPER, SHADOW, and LIVE environments.
Uses cryptographic marker files to detect and fail closed on cross-environment crossover.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import EnvironmentIsolationError

ENV_MARKER_FILENAME = ".alphaforge_env_marker"


class EnvironmentDirectoryManager:
    """
    Manages and enforces runtime directory isolation.

    Guarantees:
    - Each environment operates strictly in its designated runtime tree.
    - An environment cannot overwrite or attach to another environment's runtime directory.
    - Crossover attempts immediately raise EnvironmentIsolationError.
    """

    @classmethod
    def get_env_root(cls, config: DeploymentConfig) -> Path:
        """Return the root path allocated for this specific environment."""
        return config.runtime_root / config.environment.value.lower()

    @classmethod
    def initialize_directories(cls, config: DeploymentConfig) -> dict[str, Path]:
        """
        Create isolated directories for state, logs, audit, and observability.
        Validates environment marker to prevent accidental crossover.
        """
        env_root = cls.get_env_root(config)
        env_root.mkdir(parents=True, exist_ok=True)

        # Marker validation / creation
        cls._verify_or_create_marker(env_root, config.environment, config.schema_version)

        # Effective subdirectories
        state_dir = config.effective_state_dir
        log_dir = config.effective_log_dir
        audit_dir = config.effective_audit_dir
        obs_dir = config.effective_observability_dir

        for d in (state_dir, log_dir, audit_dir, obs_dir):
            d.mkdir(parents=True, exist_ok=True)
            # If d is inside an alien environment tree, raise error
            cls._verify_path_not_in_alien_environment(d, config.environment, config.runtime_root)

        return {
            "root": env_root,
            "state": state_dir,
            "logs": log_dir,
            "audit": audit_dir,
            "observability": obs_dir,
        }

    @classmethod
    def validate_directory_isolation(cls, config: DeploymentConfig) -> None:
        """
        Verify that all configured paths comply with environment isolation rules.
        """
        env_root = cls.get_env_root(config)
        if env_root.exists():
            cls._verify_marker_match(env_root, config.environment)

        for d in (
            config.effective_state_dir,
            config.effective_log_dir,
            config.effective_audit_dir,
            config.effective_observability_dir,
        ):
            if d.exists():
                cls._verify_path_not_in_alien_environment(
                    d, config.environment, config.runtime_root
                )

    @classmethod
    def _verify_or_create_marker(
        cls,
        target_dir: Path,
        environment: DeploymentEnvironment,
        schema_version: str,
    ) -> None:
        marker_path = target_dir / ENV_MARKER_FILENAME
        if marker_path.exists():
            cls._verify_marker_match(target_dir, environment)
        else:
            marker_data: dict[str, Any] = {
                "environment": environment.value,
                "schema_version": schema_version,
            }
            marker_path.write_text(
                json.dumps(marker_data, sort_keys=True, indent=2),
                encoding="utf-8",
            )

    @classmethod
    def _verify_marker_match(cls, target_dir: Path, expected_env: DeploymentEnvironment) -> None:
        marker_path = target_dir / ENV_MARKER_FILENAME
        if not marker_path.exists():
            return

        try:
            content = marker_path.read_text(encoding="utf-8")
            data = json.loads(content)
            marked_env_str = data.get("environment")
        except Exception as exc:
            raise EnvironmentIsolationError(
                f"Corrupt environment marker at '{marker_path}': {exc}. Halting startup."
            ) from exc

        if marked_env_str != expected_env.value:
            msg = (
                f"Environment crossover violation! Directory '{target_dir}' is marked for "
                f"environment '{marked_env_str}', "
                f"but current deployment is '{expected_env.value}'. "
                f"Access rejected to protect state integrity."
            )
            raise EnvironmentIsolationError(msg)

    @classmethod
    def _verify_path_not_in_alien_environment(
        cls,
        check_path: Path,
        current_env: DeploymentEnvironment,
        runtime_root: Path,
    ) -> None:
        """
        Ensure check_path does not point inside the runtime directory of a different environment.
        For example, a PAPER configuration pointing state_dir to runtime/live/state.
        """
        check_resolved = check_path.resolve()
        for alien_env in DeploymentEnvironment:
            if alien_env == current_env:
                continue
            alien_root = (runtime_root / alien_env.value.lower()).resolve()
            # If check_resolved is inside alien_root
            if check_resolved == alien_root or alien_root in check_resolved.parents:
                msg = (
                    f"Cross-environment path collision! Path '{check_path}' is inside the "
                    f"runtime tree of environment '{alien_env.value}', while current environment "
                    f"is '{current_env.value}'. "
                    f"Cross-environment directory access is strictly forbidden."
                )
                raise EnvironmentIsolationError(msg)
