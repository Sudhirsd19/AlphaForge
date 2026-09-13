"""
AlphaForge Phase 13 Security Tests: Secrets Protection & Redaction.

Verifies SEC6, SEC7, SEC11, SEC14, and ADV-SEC-4.
"""

import io
import logging

import pytest

from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    CredentialIsolationError,
    SecurityConfigurationError,
)
from alphaforge.security.invariants import (
    assert_no_secret_in_persisted_state,
    assert_no_secret_in_text,
)
from alphaforge.security.redaction import RedactionFormatter, redact_text


def test_sec6_secrets_not_in_logs() -> None:
    """SEC6: Secrets are automatically scrubbed from logs."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    formatter = RedactionFormatter(fmt="%(levelname)s - %(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_sec6_logger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    secret_key = "TopSecretLiveAPIKey999"  # noqa: S105
    formatter.register_secret(secret_key)

    logger.info("Initializing broker with api_key=%s", secret_key)
    logger.info("Headers: Authorization: Bearer MySecretBearerToken123")
    logger.info("Credentials payload: {'api_secret': 'SuperSecretValueXYZ'}")

    handler.flush()
    log_output = log_stream.getvalue()

    # Invariant assertions
    assert secret_key not in log_output
    assert "MySecretBearerToken123" not in log_output
    assert "SuperSecretValueXYZ" not in log_output
    assert "********" in log_output
    assert_no_secret_in_text(log_output, secret_key)


def test_sec7_secrets_not_in_exceptions_or_representations() -> None:
    """SEC7: SecretValue does not leak in __str__, __repr__, or format."""
    secret = "HighlyConfidentialSecret123"  # noqa: S105
    sv = SecretValue(secret)

    # String representations
    assert str(sv) == "********"
    assert repr(sv) == "SecretValue('********')"
    assert f"{sv}" == "********"
    assert secret not in str(sv)
    assert secret not in repr(sv)

    # Plaintext retrieval requires explicit method call
    assert sv.get_secret_value() == secret

    # Invariant assertion
    assert_no_secret_in_text(str(sv), secret)
    assert_no_secret_in_text(repr(sv), secret)

    # Empty secret cannot be created
    with pytest.raises(SecurityConfigurationError):
        SecretValue("")
    with pytest.raises(SecurityConfigurationError):
        SecretValue("   ")


def test_sec11_persisted_recovery_state_does_not_contain_credentials() -> None:
    """SEC11: Persisted models and dumped state do not leak secrets."""
    secret_key = "LiveAuthenticKey998877"  # noqa: S105
    secret_val = "LiveAuthenticSecret998877"  # noqa: S105

    creds = BrokerCredentials(
        broker_id="zerodha",
        api_key=SecretValue(secret_key),
        api_secret=SecretValue(secret_val),
        account_id="AB123456",
        is_live=True,
    )

    # Standard model dump
    dumped_json = creds.model_dump_json()
    assert secret_key not in dumped_json
    assert secret_val not in dumped_json

    # Invariant assertions
    assert_no_secret_in_persisted_state(creds, secret_key)
    assert_no_secret_in_persisted_state(creds, secret_val)

    # Dictionary representation
    dumped_dict = creds.model_dump()
    assert_no_secret_in_persisted_state(dumped_dict, secret_key)


def test_sec14_credential_isolation_paper_vs_live() -> None:
    """SEC14: Credential isolation between paper and live environments."""
    paper_creds = BrokerCredentials(
        broker_id="paper_broker",
        api_key=SecretValue("paper_mock_api_key_1"),
        api_secret=SecretValue("paper_mock_api_secret_1"),
        account_id="PAPER_ACC_100",
        is_live=False,
    )

    live_creds = BrokerCredentials(
        broker_id="zerodha",
        api_key=SecretValue("RealLiveProductionKeyABC123"),
        api_secret=SecretValue("RealLiveProductionSecretXYZ987"),
        account_id="PROD_ACC_100",
        is_live=True,
    )

    store = CredentialStore()
    store.set_paper_credentials(paper_creds)
    store.set_live_credentials(live_creds)

    # In paper mode, paper creds returned
    retrieved_paper = store.get_credentials(TradingMode.PAPER)
    assert retrieved_paper.account_id == "PAPER_ACC_100"
    assert not retrieved_paper.is_live

    # In shadow mode, paper creds returned
    retrieved_shadow = store.get_credentials(TradingMode.SHADOW)
    assert retrieved_shadow.account_id == "PAPER_ACC_100"
    assert not retrieved_shadow.is_live

    # In live mode, live creds returned
    retrieved_live = store.get_credentials(TradingMode.LIVE)
    assert retrieved_live.account_id == "PROD_ACC_100"
    assert retrieved_live.is_live

    # Setting paper creds as live must fail closed
    with pytest.raises(CredentialIsolationError):
        store.set_live_credentials(paper_creds)


def test_adv_sec4_injected_secret_with_special_characters() -> None:
    """
    ADV-SEC-4: Injected secret with special characters never leaks into logs or state.
    """
    weird_secret = 'p@$$w0rd!#%^&*()_+=~`{}[]|:;"<>,.?/'  # noqa: S105
    sv = SecretValue(weird_secret)

    # Check formatting
    assert str(sv) == "********"
    assert repr(sv) == "SecretValue('********')"

    # Check redaction of text containing this secret
    log_msg = f"User authenticated with password: {weird_secret} on port 8080"
    redacted = redact_text(log_msg, custom_secrets=[weird_secret])

    assert weird_secret not in redacted
    assert "********" in redacted
    assert_no_secret_in_text(redacted, weird_secret)

    # In logs
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = RedactionFormatter(custom_secrets=[weird_secret])
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_adv_sec4_logger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    logger.info("Connecting with secret: %s", weird_secret)
    handler.flush()

    output = stream.getvalue()
    assert weird_secret not in output
    assert_no_secret_in_text(output, weird_secret)
