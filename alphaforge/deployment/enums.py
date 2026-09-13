"""
AlphaForge Deployment Enumerations.

Deterministic string enumerations for execution deployment environments.
Enforces PAPER as authoritative default and fails closed on any malformed or unknown values.
"""

from __future__ import annotations

from enum import StrEnum

from alphaforge.deployment.exceptions import DeploymentConfigurationError


class DeploymentEnvironment(StrEnum):
    """Authoritative deployment runtime environment."""

    DEV = "DEV"
    TEST = "TEST"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"

    @classmethod
    def default(cls) -> DeploymentEnvironment:
        """Return the authoritative default environment (PAPER)."""
        return cls.PAPER

    @classmethod
    def from_str(cls, value: str | DeploymentEnvironment) -> DeploymentEnvironment:
        """
        Strictly parse deployment environment from string.

        Fails closed with DeploymentConfigurationError if value is:
        - non-string (and not already DeploymentEnvironment)
        - empty or whitespace-only
        - unknown or invalid environment name

        An explicit invalid value is NEVER conflated with unset/missing.
        """
        if isinstance(value, cls):
            return value

        if not isinstance(value, str):
            raise DeploymentConfigurationError(
                f"DeploymentEnvironment must be a string or DeploymentEnvironment instance, "
                f"got {type(value).__name__}"
            )

        cleaned = value.strip()
        if not cleaned:
            raise DeploymentConfigurationError(
                "DeploymentEnvironment cannot be empty or whitespace. "
                "Specify a valid environment or leave unset to default to PAPER."
            )

        upper_val = cleaned.upper()
        try:
            return cls(upper_val)
        except ValueError:
            valid_modes = [e.value for e in cls]
            raise DeploymentConfigurationError(
                f"Invalid deployment environment: '{value}'. Expected one of: {valid_modes}"
            ) from None

    @property
    def is_live(self) -> bool:
        """Return True if environment is LIVE."""
        return self == DeploymentEnvironment.LIVE

    @property
    def is_simulated(self) -> bool:
        """Return True if execution environment is strictly simulated (non-live)."""
        return self in (
            DeploymentEnvironment.DEV,
            DeploymentEnvironment.TEST,
            DeploymentEnvironment.PAPER,
            DeploymentEnvironment.SHADOW,
        )
