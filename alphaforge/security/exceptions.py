"""
AlphaForge Security Exceptions.

Fail-closed security hierarchy for personal/private algorithmic trading.
"""

from alphaforge.core.exceptions import AlphaForgeError


class SecurityError(AlphaForgeError):
    """Base exception for all security, credential, and authorization failures."""

    pass


class SecurityConfigurationError(SecurityError):
    """Raised when security or trading configuration is invalid, ambiguous, or malformed."""

    pass


class SecurityStartupError(SecurityError):
    """Raised when one or more startup security gate checks fail."""

    pass


class SecurityAuthorizationError(SecurityError):
    """Raised when an action or order submission is not authorized."""

    pass


class KillSwitchEngagedError(SecurityAuthorizationError):
    """Raised when an action is rejected because the emergency kill switch is engaged."""

    pass


class ReconciliationGateClosedError(SecurityAuthorizationError):
    """Raised when trading is blocked because the reconciliation gate has not passed."""

    pass


class CredentialIsolationError(SecurityError):
    """Raised when credential isolation or integrity invariants are violated."""

    pass


class MissingCredentialsError(CredentialIsolationError):
    """Raised when credentials required for the requested trading mode are missing."""

    pass


class SecretLeakError(SecurityError):
    """Raised when unredacted secrets are detected in text, logs, or persisted state."""

    pass
