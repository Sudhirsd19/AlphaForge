"""
AlphaForge Security Enumerations.

Deterministic string enumerations for trading mode and kill switch state.
"""

from __future__ import annotations

from enum import StrEnum

from alphaforge.security.exceptions import SecurityConfigurationError


class TradingMode(StrEnum):
    """Authoritative trading execution mode."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"

    @classmethod
    def from_str(cls, value: str | TradingMode) -> TradingMode:
        """
        Parse trading mode strictly from string.

        Fails closed with SecurityConfigurationError if string is unknown or malformed.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise SecurityConfigurationError(
                f"TradingMode must be a string or TradingMode instance, got {type(value).__name__}"
            )

        cleaned = value.strip().upper()
        try:
            return cls(cleaned)
        except ValueError:
            raise SecurityConfigurationError(
                f"Invalid TRADING_MODE: '{value}'. Expected one of: {[m.value for m in cls]}"
            ) from None


class KillSwitchStatus(StrEnum):
    """Operational status of the emergency kill switch."""

    DISARMED = "DISARMED"
    ENGAGED = "ENGAGED"

    @classmethod
    def from_str(cls, value: str | KillSwitchStatus) -> KillSwitchStatus:
        """
        Parse kill switch status strictly from string.

        Fails closed with SecurityConfigurationError if string is unknown or malformed.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            msg = (
                f"KillSwitchStatus must be a string or KillSwitchStatus instance, "
                f"got {type(value).__name__}"
            )
            raise SecurityConfigurationError(msg)

        cleaned = value.strip().upper()
        try:
            return cls(cleaned)
        except ValueError:
            raise SecurityConfigurationError(
                f"Invalid KillSwitchStatus: '{value}'. Expected one of: {[m.value for m in cls]}"
            ) from None
