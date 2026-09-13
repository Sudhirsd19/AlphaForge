"""
AlphaForge Deployment Environments Package.

Deterministic runtime boundary and environment isolation across DEV, TEST, PAPER, SHADOW, and LIVE.
"""

from alphaforge.deployment.backup import DeterministicBackupManager
from alphaforge.deployment.broker_guard import (
    DeploymentBrokerGuard,
    is_live_execution_broker,
    unwrap_broker,
)
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import (
    DeploymentConfigurationError,
    DeploymentError,
    DeploymentSafetyError,
    EnvironmentIsolationError,
    ShutdownError,
    StartupValidationError,
)
from alphaforge.deployment.identity import (
    DeploymentIdentity,
    compute_config_hash,
    get_deployment_identity,
    resolve_git_revision,
)
from alphaforge.deployment.isolation import EnvironmentDirectoryManager
from alphaforge.deployment.readiness import DeploymentReadinessChecker, ReadinessReport
from alphaforge.deployment.rollback import RollbackCoordinator, RollbackValidationResult
from alphaforge.deployment.runtime import DeploymentRuntime

__all__ = [
    "DeploymentBrokerGuard",
    "DeploymentConfig",
    "DeploymentConfigurationError",
    "DeploymentEnvironment",
    "DeploymentError",
    "DeploymentIdentity",
    "DeploymentReadinessChecker",
    "DeploymentRuntime",
    "DeploymentSafetyError",
    "DeterministicBackupManager",
    "EnvironmentDirectoryManager",
    "EnvironmentIsolationError",
    "ReadinessReport",
    "RollbackCoordinator",
    "RollbackValidationResult",
    "ShutdownError",
    "StartupValidationError",
    "compute_config_hash",
    "get_deployment_identity",
    "is_live_execution_broker",
    "resolve_git_revision",
    "unwrap_broker",
]
