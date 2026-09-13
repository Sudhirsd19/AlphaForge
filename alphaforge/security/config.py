"""
AlphaForge Security & Trading Mode Configuration.

Strict, fail-closed configuration parsing and validation for personal trading systems.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.security.enums import KillSwitchStatus, TradingMode
from alphaforge.security.exceptions import SecurityConfigurationError

TRUE_VALUES: frozenset[str] = frozenset({"true", "1", "yes", "y", "t", "on"})
FALSE_VALUES: frozenset[str] = frozenset({"false", "0", "no", "n", "f", "off"})


def parse_strict_bool(val: Any) -> bool:
    """
    Strictly parse boolean value.

    Rejects ambiguous, empty, or unknown strings and non-boolean types.
    Fails closed with SecurityConfigurationError.
    """
    if isinstance(val, bool):
        return val

    if isinstance(val, int):
        if val == 1:
            return True
        elif val == 0:
            return False
        raise SecurityConfigurationError(
            f"Ambiguous integer boolean value: {val}. Expected 0 or 1."
        )

    if isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in TRUE_VALUES:
            return True
        if cleaned in FALSE_VALUES:
            return False
        expected = sorted(TRUE_VALUES | FALSE_VALUES)
        raise SecurityConfigurationError(
            f"Ambiguous or invalid boolean string: '{val}'. Expected one of {expected}"
        )

    raise SecurityConfigurationError(
        f"Cannot parse boolean from unsupported type {type(val).__name__}: {val!r}"
    )


class TradingModeConfig(BaseModel):
    """
    Authoritative configuration for trading mode.

    Enforces paper-by-default and fail-closed dual opt-in for live trading.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="Target execution mode (PAPER, SHADOW, LIVE). Default is PAPER.",
    )
    live_trading_enabled: bool = Field(
        default=False,
        description="Explicit boolean confirmation to allow LIVE trading. Default is False.",
    )
    require_dual_auth_for_live: bool = Field(
        default=True,
        description="Whether LIVE mode requires TRADING_MODE=LIVE and live_trading_enabled=True.",
    )
    allow_shadow_mode: bool = Field(
        default=True,
        description="Whether SHADOW mode is permitted.",
    )

    @field_validator("trading_mode", mode="before")
    @classmethod
    def validate_trading_mode(cls, val: Any) -> TradingMode:
        return TradingMode.from_str(val)

    @field_validator(
        "live_trading_enabled",
        "require_dual_auth_for_live",
        "allow_shadow_mode",
        mode="before",
    )
    @classmethod
    def validate_strict_booleans(cls, val: Any) -> bool:
        return parse_strict_bool(val)

    @model_validator(mode="after")
    def validate_dual_opt_in(self) -> TradingModeConfig:
        if (
            self.trading_mode == TradingMode.LIVE
            and self.require_dual_auth_for_live
            and not self.live_trading_enabled
        ):
            raise SecurityConfigurationError(
                "Live trading rejected: TRADING_MODE is LIVE but live_trading_enabled is False. "
                "Both must be explicitly configured to enable live trading."
            )

        if self.trading_mode == TradingMode.SHADOW and not self.allow_shadow_mode:
            raise SecurityConfigurationError("SHADOW mode is disabled by configuration.")

        return self

    @property
    def is_live_authorized(self) -> bool:
        """Return True only if dual-key authorization for LIVE trading is completely satisfied."""
        return self.trading_mode == TradingMode.LIVE and self.live_trading_enabled is True

    @property
    def effective_mode(self) -> TradingMode:
        """Return the effective, authoritative trading mode."""
        return self.trading_mode


class SecurityConfig(BaseModel):
    """
    Global security configuration for personal trading systems.

    Implements immutable security settings and fails closed on unauthorized relaxation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_mode_config: TradingModeConfig = Field(default_factory=TradingModeConfig)
    kill_switch_enabled: bool = Field(default=True)
    kill_switch_initial_status: KillSwitchStatus = Field(default=KillSwitchStatus.DISARMED)
    enforce_risk_controls: bool = Field(default=True)
    enforce_reconciliation_gate: bool = Field(default=True)
    enforce_startup_gate: bool = Field(default=True)
    redact_logs: bool = Field(default=True)
    max_daily_loss_limit: Decimal | None = Field(default=None)

    @field_validator(
        "enforce_risk_controls",
        "enforce_reconciliation_gate",
        "enforce_startup_gate",
        "redact_logs",
        mode="before",
    )
    @classmethod
    def validate_security_booleans(cls, val: Any) -> bool:
        return parse_strict_bool(val)

    @field_validator("kill_switch_enabled", mode="before")
    @classmethod
    def validate_kill_switch_enabled(cls, val: Any) -> bool:
        parsed = parse_strict_bool(val)
        if not parsed:
            raise SecurityConfigurationError(
                "Security violation: kill_switch_enabled cannot be False. "
                "Kill switch protection cannot be disabled."
            )
        return True

    @field_validator("kill_switch_initial_status", mode="before")
    @classmethod
    def validate_kill_switch_status(cls, val: Any) -> KillSwitchStatus:
        return KillSwitchStatus.from_str(val)

    @model_validator(mode="after")
    def validate_non_relaxable_security_invariants(self) -> SecurityConfig:
        if not self.enforce_risk_controls:
            raise SecurityConfigurationError(
                "Security violation: enforce_risk_controls cannot be set to False. "
                "Risk controls are mandatory and cannot be disabled."
            )
        if not self.enforce_reconciliation_gate:
            raise SecurityConfigurationError(
                "Security violation: enforce_reconciliation_gate cannot be set to False. "
                "Reconciliation gate is mandatory and cannot be disabled."
            )
        if not self.enforce_startup_gate:
            raise SecurityConfigurationError(
                "Security violation: enforce_startup_gate cannot be set to False. "
                "Startup gate is mandatory and cannot be disabled."
            )
        if not self.kill_switch_enabled:
            raise SecurityConfigurationError(
                "Security violation: kill_switch_enabled cannot be set to False. "
                "Kill switch infrastructure must always remain active."
            )
        if self.max_daily_loss_limit is not None and self.max_daily_loss_limit <= Decimal("0"):
            raise SecurityConfigurationError(
                f"max_daily_loss_limit must be strictly positive, got {self.max_daily_loss_limit}"
            )
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SecurityConfig:
        """
        Construct SecurityConfig from an environment dictionary.

        If env is None, falls back to empty dictionary (defaulting to safe paper settings).
        Fails closed on any unknown, malformed, or invalid values.
        """
        if env is None:
            return cls()

        data: dict[str, Any] = {}
        mode_data: dict[str, Any] = {}

        if "TRADING_MODE" in env:
            mode_data["trading_mode"] = env["TRADING_MODE"]
        if "LIVE_TRADING_ENABLED" in env:
            mode_data["live_trading_enabled"] = env["LIVE_TRADING_ENABLED"]
        if "REQUIRE_DUAL_AUTH_FOR_LIVE" in env:
            mode_data["require_dual_auth_for_live"] = env["REQUIRE_DUAL_AUTH_FOR_LIVE"]
        if "ALLOW_SHADOW_MODE" in env:
            mode_data["allow_shadow_mode"] = env["ALLOW_SHADOW_MODE"]

        if mode_data:
            data["trading_mode_config"] = TradingModeConfig(**mode_data)

        if "KILL_SWITCH_ENABLED" in env:
            data["kill_switch_enabled"] = env["KILL_SWITCH_ENABLED"]
        if "KILL_SWITCH_STATUS" in env:
            data["kill_switch_initial_status"] = env["KILL_SWITCH_STATUS"]
        if "ENFORCE_RISK_CONTROLS" in env:
            data["enforce_risk_controls"] = env["ENFORCE_RISK_CONTROLS"]
        if "ENFORCE_RECONCILIATION_GATE" in env:
            data["enforce_reconciliation_gate"] = env["ENFORCE_RECONCILIATION_GATE"]
        if "ENFORCE_STARTUP_GATE" in env:
            data["enforce_startup_gate"] = env["ENFORCE_STARTUP_GATE"]
        if "REDACT_LOGS" in env:
            data["redact_logs"] = env["REDACT_LOGS"]
        if "MAX_DAILY_LOSS_LIMIT" in env:
            try:
                data["max_daily_loss_limit"] = Decimal(env["MAX_DAILY_LOSS_LIMIT"])
            except (InvalidOperation, ValueError, TypeError) as e:
                raw_limit = env["MAX_DAILY_LOSS_LIMIT"]
                raise SecurityConfigurationError(
                    f"Invalid MAX_DAILY_LOSS_LIMIT: '{raw_limit}'. Expected decimal number."
                ) from e

        return cls(**data)
