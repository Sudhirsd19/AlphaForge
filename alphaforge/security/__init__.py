"""
AlphaForge Personal Security Hardening.

Fail-closed security infrastructure designed specifically for personal algorithmic trading:
- Strict paper-first execution by default.
- Dual-key explicit live authorization.
- Credential isolation and non-leaking secret values.
- Thread-safe operational emergency kill switch.
- Automatic secret redaction for logging and errors.
- Pre-flight security startup gate.
- Secure broker wrapper enforcing pre-trade safety checks.
"""

from alphaforge.security.authorizer import SecureBroker, SecurityAuthorizer
from alphaforge.security.config import SecurityConfig, TradingModeConfig, parse_strict_bool
from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import (
    CredentialIsolationError,
    KillSwitchEngagedError,
    MissingCredentialsError,
    ReconciliationGateClosedError,
    SecretLeakError,
    SecurityAuthorizationError,
    SecurityConfigurationError,
    SecurityError,
    SecurityStartupError,
)
from alphaforge.security.invariants import (
    assert_invalid_mode_fails_closed,
    assert_kill_switch_blocks_entry,
    assert_live_trading_disabled_by_default,
    assert_no_secret_in_persisted_state,
    assert_no_secret_in_text,
    assert_reconciliation_gate_required,
    assert_risk_controls_present,
)
from alphaforge.security.kill_switch import KillSwitch, KillSwitchAuditEvent
from alphaforge.security.redaction import RedactionFormatter, redact_text
from alphaforge.security.startup import SecurityStartupGate, StartupSecurityReport

__all__ = [
    # Authorizer & Broker
    "SecurityAuthorizer",
    "SecureBroker",
    # Config
    "SecurityConfig",
    "TradingModeConfig",
    "parse_strict_bool",
    # Credentials
    "BrokerCredentials",
    "CredentialStore",
    "SecretValue",
    # Enums
    "KillSwitchStatus",
    "TradingMode",
    # Exceptions
    "SecurityError",
    "SecurityConfigurationError",
    "SecurityStartupError",
    "SecurityAuthorizationError",
    "KillSwitchEngagedError",
    "ReconciliationGateClosedError",
    "CredentialIsolationError",
    "MissingCredentialsError",
    "SecretLeakError",
    # Invariants
    "assert_live_trading_disabled_by_default",
    "assert_invalid_mode_fails_closed",
    "assert_no_secret_in_text",
    "assert_no_secret_in_persisted_state",
    "assert_risk_controls_present",
    "assert_kill_switch_blocks_entry",
    "assert_reconciliation_gate_required",
    # Kill switch
    "KillSwitch",
    "KillSwitchAuditEvent",
    # Redaction
    "RedactionFormatter",
    "redact_text",
    # Startup
    "SecurityStartupGate",
    "StartupSecurityReport",
]
