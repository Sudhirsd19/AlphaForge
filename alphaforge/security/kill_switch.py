"""
AlphaForge Emergency Kill Switch.

Thread-safe emergency stop mechanism for halting all new trade orders.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from alphaforge.security.enums import KillSwitchStatus
from alphaforge.security.exceptions import KillSwitchEngagedError


class KillSwitchAuditEvent(BaseModel):
    """Immutable audit record of a kill switch state transition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str = Field(..., description="Action performed: 'ENGAGE' or 'DISARM'")
    reason: str = Field(..., description="Human-readable reason for state change")
    operator: str = Field(
        default="local_operator",
        description="Operator identity or component name",
    )


class KillSwitch:
    """
    Thread-safe operational kill switch for personal trading installations.

    When ENGAGED, all new order submissions must be immediately rejected.
    Records an immutable audit trail of all transitions.
    """

    def __init__(self, initial_status: KillSwitchStatus = KillSwitchStatus.DISARMED) -> None:
        self._lock = threading.RLock()
        self._status: KillSwitchStatus = initial_status
        self._audit_trail: list[KillSwitchAuditEvent] = []
        self._latest_reason: str = (
            "Initial configuration: ENGAGED"
            if initial_status == KillSwitchStatus.ENGAGED
            else "Initial configuration: DISARMED"
        )

        # Record initial event
        self._audit_trail.append(
            KillSwitchAuditEvent(
                action="INITIALIZE",
                reason=self._latest_reason,
                operator="system",
            )
        )

    @property
    def status(self) -> KillSwitchStatus:
        with self._lock:
            return self._status

    @property
    def latest_reason(self) -> str:
        with self._lock:
            return self._latest_reason

    def is_engaged(self) -> bool:
        with self._lock:
            return self._status == KillSwitchStatus.ENGAGED

    def is_disarmed(self) -> bool:
        with self._lock:
            return self._status == KillSwitchStatus.DISARMED

    def engage(self, reason: str, operator: str = "local_operator") -> None:
        """Engage the kill switch, immediately blocking trading."""
        with self._lock:
            self._status = KillSwitchStatus.ENGAGED
            self._latest_reason = reason
            self._audit_trail.append(
                KillSwitchAuditEvent(
                    action="ENGAGE",
                    reason=reason,
                    operator=operator,
                )
            )

    def disarm(self, reason: str, operator: str = "local_operator") -> None:
        """Disarm the kill switch, permitting trading checks to proceed."""
        with self._lock:
            self._status = KillSwitchStatus.DISARMED
            self._latest_reason = reason
            self._audit_trail.append(
                KillSwitchAuditEvent(
                    action="DISARM",
                    reason=reason,
                    operator=operator,
                )
            )

    def assert_disarmed(self) -> None:
        """
        Assert the kill switch is disarmed.

        Raises KillSwitchEngagedError if engaged.
        """
        with self._lock:
            if self._status == KillSwitchStatus.ENGAGED:
                raise KillSwitchEngagedError(
                    f"Kill switch is ENGAGED. Trading blocked. Reason: {self._latest_reason}"
                )

    def get_audit_trail(self) -> list[KillSwitchAuditEvent]:
        """Return a snapshot of the kill switch audit trail."""
        with self._lock:
            return list(self._audit_trail)
