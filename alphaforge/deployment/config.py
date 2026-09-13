"""
AlphaForge Deployment Configuration.

Strict, fail-closed configuration layer for deployment environments.
Enforces PAPER by default, dual-opt-in for LIVE, path isolation, and secret protection.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import (
    DeploymentConfigurationError,
    DeploymentSafetyError,
)
from alphaforge.security.config import parse_strict_bool
from alphaforge.security.enums import TradingMode


class DeploymentConfig(BaseModel):
    """
    Authoritative configuration for deployment environment and runtime boundary.

    Guarantees:
    - Default environment is PAPER.
    - Explicit empty/malformed/unknown environment fails closed.
    - LIVE environment requires explicit live_authorized=True.
    - Deterministic runtime directory resolution.
    - Zero secret leakage in representation or logs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: DeploymentEnvironment = Field(
        default=DeploymentEnvironment.PAPER,
        description="Deployment runtime environment (DEV, TEST, PAPER, SHADOW, LIVE).",
    )
    schema_version: str = Field(
        default="1.0.0",
        description="Configuration schema version.",
    )
    runtime_root: Path = Field(
        default=Path("runtime"),
        description="Root directory for environment runtime data.",
    )
    state_dir: Path | None = Field(
        default=None,
        description="Explicit state directory override (isolated per environment).",
    )
    log_dir: Path | None = Field(
        default=None,
        description="Explicit log directory override (isolated per environment).",
    )
    audit_dir: Path | None = Field(
        default=None,
        description="Explicit audit directory override (isolated per environment).",
    )
    observability_dir: Path | None = Field(
        default=None,
        description="Explicit observability directory override (isolated per environment).",
    )
    credential_source: str = Field(
        default="env",
        description="Metadata describing credential source (e.g. 'env', 'keyring').",
    )
    live_authorized: bool = Field(
        default=False,
        description="Explicit confirmation enabling LIVE deployment. Default is False.",
    )
    allow_shadow_mode: bool = Field(
        default=True,
        description="Whether SHADOW deployment is allowed.",
    )

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, val: Any) -> DeploymentEnvironment:
        return DeploymentEnvironment.from_str(val)

    @field_validator("live_authorized", "allow_shadow_mode", mode="before")
    @classmethod
    def validate_strict_booleans(cls, val: Any) -> bool:
        return parse_strict_bool(val)

    @field_validator(
        "runtime_root", "state_dir", "log_dir", "audit_dir", "observability_dir", mode="before"
    )
    @classmethod
    def validate_path_types(cls, val: Any) -> Path | None:
        if val is None:
            return None
        if isinstance(val, Path):
            return val
        if isinstance(val, str):
            cleaned = val.strip()
            if not cleaned:
                raise DeploymentConfigurationError("Directory path cannot be empty or whitespace.")
            return Path(cleaned)
        raise DeploymentConfigurationError(f"Invalid path type: {type(val).__name__}")

    @model_validator(mode="after")
    def validate_deployment_invariants(self) -> DeploymentConfig:
        # LIVE requires explicit live_authorized confirmation
        if self.environment == DeploymentEnvironment.LIVE and not self.live_authorized:
            raise DeploymentSafetyError(
                "Deployment rejected: Environment is LIVE but live_authorized is False. "
                "LIVE deployment requires explicit dual authorization."
            )

        # SHADOW mode check
        if self.environment == DeploymentEnvironment.SHADOW and not self.allow_shadow_mode:
            raise DeploymentSafetyError("Deployment rejected: SHADOW environment is disabled.")

        # Path traversal guard: verify none of the explicit paths traverse up via '..'
        for name, path in [
            ("runtime_root", self.runtime_root),
            ("state_dir", self.state_dir),
            ("log_dir", self.log_dir),
            ("audit_dir", self.audit_dir),
            ("observability_dir", self.observability_dir),
        ]:
            if path is not None and ".." in path.parts:
                msg = (
                    f"Path traversal detected in '{name}': '{path}'. "
                    f"Directory escaping is prohibited."
                )
                raise DeploymentConfigurationError(msg)

        return self

    @property
    def effective_state_dir(self) -> Path:
        """Resolved, deterministic state directory for this environment."""
        if self.state_dir is not None:
            return self.state_dir
        return self.runtime_root / self.environment.value.lower() / "state"

    @property
    def effective_log_dir(self) -> Path:
        """Resolved, deterministic log directory for this environment."""
        if self.log_dir is not None:
            return self.log_dir
        return self.runtime_root / self.environment.value.lower() / "logs"

    @property
    def effective_audit_dir(self) -> Path:
        """Resolved, deterministic audit directory for this environment."""
        if self.audit_dir is not None:
            return self.audit_dir
        return self.runtime_root / self.environment.value.lower() / "audit"

    @property
    def effective_observability_dir(self) -> Path:
        """Resolved, deterministic observability directory for this environment."""
        if self.observability_dir is not None:
            return self.observability_dir
        return self.runtime_root / self.environment.value.lower() / "observability"

    @property
    def matching_trading_mode(self) -> TradingMode:
        """Map deployment environment to corresponding security TradingMode."""
        match self.environment:
            case DeploymentEnvironment.LIVE:
                return TradingMode.LIVE
            case DeploymentEnvironment.SHADOW:
                return TradingMode.SHADOW
            case _:
                return TradingMode.PAPER

    def to_safe_dict(self) -> dict[str, Any]:
        """
        Return deterministic, sanitized configuration dictionary.
        Zero secret leakage guarantee.
        """
        return {
            "environment": self.environment.value,
            "schema_version": self.schema_version,
            "runtime_root": str(self.runtime_root),
            "state_dir": str(self.effective_state_dir),
            "log_dir": str(self.effective_log_dir),
            "audit_dir": str(self.effective_audit_dir),
            "observability_dir": str(self.effective_observability_dir),
            "credential_source": self.credential_source,
            "live_authorized": self.live_authorized,
            "allow_shadow_mode": self.allow_shadow_mode,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeploymentConfig:
        """
        Construct DeploymentConfig from environment dictionary.

        Rules:
        - If env is None, inspects os.environ.
        - Missing/unset ALPHAFORGE_ENV or DEPLOYMENT_ENV -> defaults to PAPER.
        - Explicit empty, whitespace, unknown, or malformed value -> FAILS CLOSED.
        - Never treats an explicitly supplied invalid value as missing.
        """
        source = os.environ if env is None else env

        data: dict[str, Any] = {}

        # Resolve environment variable with strict check
        raw_env: str | None = None
        if "ALPHAFORGE_ENV" in source:
            raw_env = source["ALPHAFORGE_ENV"]
        elif "DEPLOYMENT_ENV" in source:
            raw_env = source["DEPLOYMENT_ENV"]

        if raw_env is not None:
            # Explicitly supplied value - must be parsed strictly
            data["environment"] = DeploymentEnvironment.from_str(raw_env)
        else:
            # Unset / missing - safely default to PAPER
            data["environment"] = DeploymentEnvironment.PAPER

        if "SCHEMA_VERSION" in source:
            data["schema_version"] = source["SCHEMA_VERSION"]

        if "RUNTIME_ROOT" in source:
            data["runtime_root"] = source["RUNTIME_ROOT"]

        if "STATE_DIR" in source:
            data["state_dir"] = source["STATE_DIR"]

        if "LOG_DIR" in source:
            data["log_dir"] = source["LOG_DIR"]

        if "AUDIT_DIR" in source:
            data["audit_dir"] = source["AUDIT_DIR"]

        if "OBSERVABILITY_DIR" in source:
            data["observability_dir"] = source["OBSERVABILITY_DIR"]

        if "CREDENTIAL_SOURCE" in source:
            data["credential_source"] = source["CREDENTIAL_SOURCE"]

        # Dual authorization check for LIVE
        raw_live_auth = None
        if "LIVE_AUTHORIZED" in source:
            raw_live_auth = source["LIVE_AUTHORIZED"]
        elif "LIVE_TRADING_ENABLED" in source:
            raw_live_auth = source["LIVE_TRADING_ENABLED"]

        if raw_live_auth is not None:
            data["live_authorized"] = parse_strict_bool(raw_live_auth)

        if "ALLOW_SHADOW_MODE" in source:
            data["allow_shadow_mode"] = parse_strict_bool(source["ALLOW_SHADOW_MODE"])

        return cls(**data)
