"""
AlphaForge Environment Security Helper.

Provides utilities for secure environment variable handling and credential validation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class CredentialGuard:
    """
    Validates and guards against common credential security issues.

    This class provides read-only checks to identify potential credential
    misconfigurations without exposing sensitive data.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env or dict(os.environ)

    def check_env_file_exists(self, path: Path) -> bool:
        """Check if .env file exists."""
        return path.is_file()

    def check_credential_present(self, key: str) -> bool:
        """Check if a credential key is set in environment."""
        return key in self._env and bool(self._env[key].strip())

    def check_credential_not_hardcoded(self, value: str) -> bool:
        """
        Check if a credential value looks like a placeholder, not a real secret.

        Returns True if the value appears to be a placeholder/placeholder.
        """
        placeholders = {
            "your-api-key-here",
            "your-secret-here",
            "your-token-here",
            "change-me",
            "placeholder",
            "example",
            "xxx",
            "replace-with-actual-value",
        }
        return value.strip().lower() in placeholders

    def check_credential_not_too_long(self, key: str, max_length: int = 1000) -> bool:
        """
        Check if a credential length is reasonable.

        Very long values may indicate file contents being loaded incorrectly.
        """
        value = self._env.get(key, "")
        return len(value) <= max_length

    def check_trailing_newlines(self, key: str) -> bool:
        """
        Check if a credential value has trailing newlines (a common source of bugs).
        """
        value = self._env.get(key, "")
        if value != value.rstrip():
            return False
        return True

    def check_credential_format(self, key: str, expected_format: str) -> bool:
        """
        Validate credential format against known patterns.

        Args:
            key: Environment variable name
            expected_format: One of 'uuid', 'jwt', 'api_key_8-24', 'base64'

        Returns:
            True if format matches expected pattern, False otherwise
        """
        value = self._env.get(key, "").strip()
        if not value:
            return False

        if expected_format == "uuid":
            import re

            # UUID v4 pattern
            uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
            return bool(re.match(uuid_pattern, value.lower()))

        elif expected_format == "jwt":
            # JWT has 3 base64 parts separated by dots
            parts = value.split(".")
            return len(parts) == 3 and all(len(p) > 10 for p in parts)

        elif expected_format == "api_key_8-24":
            return 8 <= len(value) <= 24

        elif expected_format == "base64":
            import base64
            import binascii

            try:
                # Try to decode to verify it's valid base64
                decoded = base64.b64decode(value, validate=True)
                return len(decoded) > 0
            except binascii.Error:
                return False

        return True

    def validate_credentials(self) -> list[dict]:
        """
        Run all credential validation checks.

        Returns a list of issues found (empty list = no issues).
        """
        issues: list[dict] = []

        # Check common credential keys
        critical_keys = [
            "UPSTOX_API_KEY",
            "UPSTOX_API_SECRET",
            "UPSTOX_ACCESS_TOKEN",
            "UPSTOX_TOTP_KEY",
        ]

        for key in critical_keys:
            if key in self._env:
                value = self._env[key]
                if not value or not value.strip():
                    issues.append({"key": key, "issue": "empty_value"})
                elif len(value) > 2000:
                    issues.append({"key": key, "issue": "excessive_length"})

        return issues


def check_env_permissions(env_path: Path) -> bool:
    """
    Check if .env file has secure permissions.

    Returns True if file is only readable by owner (600).
    """
    if not env_path.is_file():
        return False

    import stat

    mode = env_path.stat().st_mode
    # Check if file is owner-readable-only (0o600)
    # owner: read+write, group: none, others: none
    expected = stat.S_IFREG | stat.S_IRUSR | stat.S_IWUSR
    return mode == expected


def load_secure_env(env_path: Path) -> dict[str, str]:
    """
    Load environment variables from .env file with security checks.

    Args:
        env_path: Path to .env file

    Returns:
        Dictionary of loaded environment variables

    Raises:
        FileNotFoundError: If .env file does not exist
        PermissionError: If .env file has insecure permissions
    """
    if not env_path.is_file():
        raise FileNotFoundError(f"Environment file not found: {env_path}")

    # Check permissions before loading
    if not check_env_permissions(env_path):
        import warnings

        warnings.warn(
            f"Environment file {env_path} has insecure permissions. "
            "Expected mode 0o600 (owner read/write only).",
            RuntimeWarning,
            stacklevel=2,
        )

    env_vars: dict[str, str] = {}

    with open(env_path, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Parse key=value
            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Remove surrounding quotes if present
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]

            if key:
                env_vars[key] = value

    return env_vars
