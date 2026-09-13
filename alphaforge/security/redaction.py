"""
AlphaForge Secret Redaction.

Log formatter and string sanitization utilities to prevent sensitive credentials leaking.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REDACTION_MASK: str = "********"

# Common patterns for credential assignments in logs, JSON, or text
GENERIC_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"""(?i)(["']?(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|auth[_-]?token|password|private[_-]?key|client[_-]?secret)["']?)"""
        r"""(\s*[:=]\s*["']?)([^"'\s,;{}]+)(["']?)"""
    ),
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|token|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)(password\s*=\s*)[^\s,;]+"),
)


def redact_text(text: str, custom_secrets: Sequence[str] | None = None) -> str:
    """
    Sanitize text by replacing sensitive credentials with REDACTION_MASK.

    Scans for well-known credential patterns and specific known secret values.
    """
    if not text:
        return text

    sanitized = text

    # First redact custom known secrets (exact matching with regex escaping)
    if custom_secrets:
        for secret in custom_secrets:
            if secret and len(secret) >= 4:
                sanitized = re.sub(re.escape(secret), REDACTION_MASK, sanitized)

    # Redact standard credential patterns
    for pat in GENERIC_CREDENTIAL_PATTERNS:
        is_auth = "authorization" in pat.pattern
        is_pwd = "password" in pat.pattern and "api" not in pat.pattern
        if is_auth or is_pwd:
            sanitized = pat.sub(r"\g<1>" + REDACTION_MASK, sanitized)
        else:
            sanitized = pat.sub(r"\g<1>\g<2>" + REDACTION_MASK + r"\g<4>", sanitized)

    return sanitized


class RedactionFormatter(logging.Formatter):
    """
    Logging formatter that scrubs sensitive credentials from log records.

    Applies pattern matching and known secret masks to formatted messages and tracebacks.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        custom_secrets: Sequence[str] | None = None,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._custom_secrets: list[str] = list(custom_secrets or [])

    def register_secret(self, secret: str) -> None:
        """Register a known secret to redact across all formatted records."""
        if secret and len(secret) >= 4 and secret not in self._custom_secrets:
            self._custom_secrets.append(secret)

    def format(self, record: logging.LogRecord) -> str:
        # Format the record standardly first (handles msg % args cleanly)
        formatted = super().format(record)
        # Redact the final output string
        return redact_text(formatted, self._custom_secrets)
