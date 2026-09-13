"""
AlphaForge Credential Protection & Isolation.

Immutable secret wrappers, broker credential schemas, and isolated credential storage.
"""

from __future__ import annotations

import hmac

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    CredentialIsolationError,
    MissingCredentialsError,
    SecurityConfigurationError,
)

FORBIDDEN_LIVE_PATTERNS: tuple[str, ...] = (
    "dummy",
    "test",
    "fake",
    "mock",
    "placeholder",
    "changeme",
    "sample",
    "default",
    "paper",
    "sandbox",
    "sim",
    "xxx",
    "12345",
)


class SecretValue(SecretStr):
    """
    Immutable value object wrapping secret strings.

    Prevents accidental leakage in logs, __str__, __repr__, and string formatting.
    Plaintext is accessible ONLY via .get_secret_value().
    """

    def __init__(self, value: str | SecretStr | SecretValue) -> None:
        if isinstance(value, (SecretStr, SecretValue)):
            raw = value.get_secret_value()
        else:
            raw = str(value)

        if not raw or not raw.strip():
            raise SecurityConfigurationError("SecretValue cannot be empty or blank")

        super().__init__(raw)

    def __repr__(self) -> str:
        return "SecretValue('********')"

    def __str__(self) -> str:
        return "********"

    def __format__(self, format_spec: str) -> str:
        return "********"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (SecretValue, SecretStr)):
            return hmac.compare_digest(self.get_secret_value(), other.get_secret_value())
        return False

    def __hash__(self) -> int:
        return hash(self.get_secret_value())


class BrokerCredentials(BaseModel):
    """
    Strongly-typed broker authentication credentials.

    Strictly separates paper vs live execution credentials.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_id: str = Field(
        ...,
        min_length=1,
        description="Target broker identifier (e.g. 'zerodha', 'paper')",
    )
    api_key: SecretValue = Field(..., description="API key (redacted)")
    api_secret: SecretValue = Field(..., description="API secret key (redacted)")
    account_id: str = Field(..., min_length=1, description="Broker account / client ID")
    is_live: bool = Field(
        default=False,
        description="Whether these credentials are for live exchange trading",
    )

    @field_validator("broker_id", "account_id")
    @classmethod
    def validate_non_empty_str(cls, val: str) -> str:
        trimmed = val.strip()
        if not trimmed:
            raise SecurityConfigurationError("Field cannot be empty or whitespace")
        return trimmed

    @model_validator(mode="after")
    def validate_credential_integrity(self) -> BrokerCredentials:
        if self.is_live:
            # Check for dummy or test credentials in LIVE mode
            key_val = self.api_key.get_secret_value().lower().strip()
            secret_val = self.api_secret.get_secret_value().lower().strip()
            account_val = self.account_id.lower().strip()

            for pat in FORBIDDEN_LIVE_PATTERNS:
                if pat in key_val or key_val.startswith(pat):
                    raise CredentialIsolationError(
                        f"Live credentials cannot use dummy or test API key (contains '{pat}')"
                    )
                if pat in secret_val or secret_val.startswith(pat):
                    raise CredentialIsolationError(
                        f"Live credentials cannot use dummy or test API secret (contains '{pat}')"
                    )
                if pat == account_val or account_val.startswith(pat):
                    raise CredentialIsolationError(
                        f"Live account ID cannot use dummy or test value: '{self.account_id}'"
                    )

            if len(key_val) < 8:
                raise CredentialIsolationError(
                    "Live API key length is suspiciously short (< 8 characters)"
                )
            if len(secret_val) < 8:
                raise CredentialIsolationError(
                    "Live API secret length is suspiciously short (< 8 characters)"
                )

        return self


class CredentialStore:
    """
    Isolated credential store for personal trading systems.

    Guarantees complete separation of paper and live credentials.
    Paper credentials can NEVER be returned when live mode is requested.
    """

    def __init__(
        self,
        paper_credentials: BrokerCredentials | None = None,
        live_credentials: BrokerCredentials | None = None,
    ) -> None:
        self._paper_credentials: BrokerCredentials | None = None
        self._live_credentials: BrokerCredentials | None = None

        if paper_credentials is not None:
            self.set_paper_credentials(paper_credentials)
        if live_credentials is not None:
            self.set_live_credentials(live_credentials)

    def set_paper_credentials(self, creds: BrokerCredentials) -> None:
        if creds.is_live:
            raise CredentialIsolationError("Cannot set live credentials as paper credentials")
        self._paper_credentials = creds

    def set_live_credentials(self, creds: BrokerCredentials) -> None:
        if not creds.is_live:
            raise CredentialIsolationError("Cannot set non-live credentials as live credentials")
        self._live_credentials = creds

    def get_credentials(self, mode: TradingMode | str) -> BrokerCredentials:
        resolved_mode = TradingMode.from_str(mode)
        if resolved_mode == TradingMode.LIVE:
            if self._live_credentials is None:
                raise MissingCredentialsError(
                    "Live trading credentials are not configured in CredentialStore"
                )
            return self._live_credentials

        if self._paper_credentials is None:
            raise MissingCredentialsError(
                f"Paper credentials are not configured for {resolved_mode.value} mode"
            )
        return self._paper_credentials

    @property
    def has_live_credentials(self) -> bool:
        return self._live_credentials is not None

    @property
    def has_paper_credentials(self) -> bool:
        return self._paper_credentials is not None
