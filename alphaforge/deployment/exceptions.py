"""
AlphaForge Deployment Exceptions.

Strict, typed exceptions for deployment environment validation, runtime boundary safety,
startup pre-flight gates, shutdown failures, and cross-environment isolation violations.
"""

from __future__ import annotations


class DeploymentError(Exception):
    """Base exception for all deployment-related errors."""


class DeploymentConfigurationError(DeploymentError):
    """Raised when deployment configuration is missing, malformed, or invalid."""


class DeploymentSafetyError(DeploymentError):
    """Raised when an environment safety invariant is breached (e.g. PAPER reaching live broker)."""


class StartupValidationError(DeploymentError):
    """Raised when pre-flight startup sequence validation fails."""


class ShutdownError(DeploymentError):
    """Raised when runtime shutdown encounters an unrecoverable failure."""


class EnvironmentIsolationError(DeploymentError):
    """Raised when an environment attempts to access or mutate resources of another environment."""
