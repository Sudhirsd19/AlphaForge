"""
AlphaForge Phase 13 Security Tests: Trading Mode & Configuration Integrity.

Verifies SEC1, SEC2, SEC3, SEC4, SEC5, SEC13, and ADV-SEC-1, ADV-SEC-2, ADV-SEC-3.
"""

import pytest
from pydantic import ValidationError

from alphaforge.security.config import SecurityConfig, TradingModeConfig, parse_strict_bool
from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    CredentialIsolationError,
    MissingCredentialsError,
    SecurityConfigurationError,
    SecurityStartupError,
)
from alphaforge.security.invariants import (
    assert_invalid_mode_fails_closed,
    assert_live_trading_disabled_by_default,
)
from alphaforge.security.startup import SecurityStartupGate


def test_sec1_live_trading_default_off() -> None:
    """SEC1: Assert live trading is disabled by default."""
    config = SecurityConfig()
    assert config.trading_mode_config.trading_mode == TradingMode.PAPER
    assert config.trading_mode_config.live_trading_enabled is False
    assert config.trading_mode_config.is_live_authorized is False
    assert config.trading_mode_config.effective_mode == TradingMode.PAPER

    assert_live_trading_disabled_by_default(config)


def test_sec2_invalid_trading_mode_fails_closed() -> None:
    """SEC2: Invalid trading mode fails closed immediately."""
    invalid_modes = ["INVALID", "paper_trading", "PROD", "TEST", "sim", ""]
    for m in invalid_modes:
        with pytest.raises(SecurityConfigurationError):
            TradingMode.from_str(m)
        with pytest.raises((SecurityConfigurationError, ValidationError)):
            TradingModeConfig(trading_mode=m)  # type: ignore[arg-type]
        assert_invalid_mode_fails_closed(m)


def test_sec3_missing_live_credential_fails_closed() -> None:
    """SEC3: Missing or dummy live credentials fail closed."""
    store = CredentialStore()
    with pytest.raises(MissingCredentialsError):
        store.get_credentials(TradingMode.LIVE)

    # Attempting to use dummy credentials for LIVE mode must fail
    with pytest.raises(CredentialIsolationError):
        BrokerCredentials(
            broker_id="zerodha",
            api_key=SecretValue("dummy_key_12345"),
            api_secret=SecretValue("secret_987654321"),
            account_id="ACC123456",
            is_live=True,
        )


def test_sec4_paper_mode_cannot_silently_become_live() -> None:
    """SEC4: Paper credentials cannot be set as live or substituted."""
    paper_creds = BrokerCredentials(
        broker_id="paper_broker",
        api_key=SecretValue("paper_key_12345"),
        api_secret=SecretValue("paper_secret_12345"),
        account_id="PAPER_ACC_1",
        is_live=False,
    )

    store = CredentialStore()
    store.set_paper_credentials(paper_creds)

    # Setting paper creds as live must raise CredentialIsolationError
    with pytest.raises(CredentialIsolationError):
        store.set_live_credentials(paper_creds)

    # Requesting LIVE credentials from paper-only store must fail closed
    with pytest.raises(MissingCredentialsError):
        store.get_credentials(TradingMode.LIVE)


def test_sec5_ambiguous_boolean_configuration_fails_closed() -> None:
    """SEC5: Ambiguous boolean configuration fails closed."""
    ambiguous_values = ["maybe", "", "unknown", "null", "none", "2", "-1", "undefined"]
    for val in ambiguous_values:
        with pytest.raises(SecurityConfigurationError):
            parse_strict_bool(val)

    with pytest.raises(SecurityConfigurationError):
        parse_strict_bool(2)

    with pytest.raises(SecurityConfigurationError):
        parse_strict_bool(None)

    # Valid boolean inputs work strictly
    assert parse_strict_bool(True) is True
    assert parse_strict_bool(False) is False
    assert parse_strict_bool("true") is True
    assert parse_strict_bool("false") is False
    assert parse_strict_bool("1") is True
    assert parse_strict_bool("0") is False
    assert parse_strict_bool("yes") is True
    assert parse_strict_bool("no") is False


def test_sec13_unknown_configuration_fails_safely() -> None:
    """SEC13: Unknown configuration keys fail safely (extra='forbid')."""
    with pytest.raises(ValidationError):
        SecurityConfig(unexpected_flag=True)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        TradingModeConfig(unknown_field="value")  # type: ignore[call-arg]


def test_adv_sec1_live_mode_missing_credentials_fails_closed() -> None:
    """ADV-SEC-1: TRADING_MODE=LIVE with missing credentials fails closed."""
    cfg = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    # Empty credential store
    empty_store = CredentialStore()
    gate = SecurityStartupGate(security_config=cfg, credential_store=empty_store)

    with pytest.raises(SecurityStartupError) as exc_info:
        gate.verify_startup()
    assert "Invalid live credentials" in str(exc_info.value)
    assert not gate.is_verified


def test_adv_sec2_typo_in_trading_mode_fails_closed() -> None:
    """ADV-SEC-2: Typo in TRADING_MODE fails closed."""
    with pytest.raises(SecurityConfigurationError):
        TradingMode.from_str("livee")

    with pytest.raises((SecurityConfigurationError, ValidationError)):
        SecurityConfig.from_env({"TRADING_MODE": "livee"})


def test_adv_sec3_conflicting_live_flags_fails_closed() -> None:
    """ADV-SEC-3: LIVE_TRADING=false + TRADING_MODE=LIVE fails closed."""
    with pytest.raises(SecurityConfigurationError):
        TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=False,
        )

    with pytest.raises(SecurityConfigurationError):
        SecurityConfig.from_env({"TRADING_MODE": "LIVE", "LIVE_TRADING_ENABLED": "false"})
