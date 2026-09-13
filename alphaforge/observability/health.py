"""
AlphaForge System Health Model.

Provides diagnostic health status representations and aggregation rollup logic.
HEALTH IS DIAGNOSTIC ONLY: HEALTHY != TRADING AUTHORIZED.
Health status NEVER overrides Phase 13 security gates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(StrEnum):
    """Component and system operational health status."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


# Deterministic priority for health rollup: higher index = higher severity
_ROLLUP_PRIORITY: list[HealthStatus] = [
    HealthStatus.HEALTHY,
    HealthStatus.DEGRADED,
    HealthStatus.BLOCKED,
    HealthStatus.FAILED,
]


class ComponentHealth(BaseModel):
    """Health record for a single system component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: HealthStatus
    message: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemHealthSnapshot(BaseModel):
    """
    Aggregated health snapshot across all monitored subsystems.

    Invariants:
    - Diagnostic visibility ONLY: this snapshot reports health telemetry and NEVER
      authorizes trading or overrides security gates.
    - Deterministic rollup priority: FAILED > BLOCKED > DEGRADED > HEALTHY.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    components: dict[str, ComponentHealth]
    overall_status: HealthStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str = ""

    @property
    def is_degraded(self) -> bool:
        return self.overall_status in (
            HealthStatus.DEGRADED,
            HealthStatus.BLOCKED,
            HealthStatus.FAILED,
        )


class HealthAggregator:
    """
    Manages and aggregates component health updates into deterministic system health snapshots.
    """

    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}

    def update_component(
        self,
        name: str,
        status: HealthStatus,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ComponentHealth:
        """Register or update health state for a named component."""
        record = ComponentHealth(
            name=name,
            status=status,
            message=message,
            metadata=metadata or {},
        )
        self._components[name] = record
        return record

    def compute_snapshot(self) -> SystemHealthSnapshot:
        """
        Aggregate all component statuses using strict severity rollup:
        FAILED > BLOCKED > DEGRADED > HEALTHY.
        """
        if not self._components:
            return SystemHealthSnapshot(
                components={},
                overall_status=HealthStatus.HEALTHY,
                message="No components registered",
            )

        # Rollup calculation
        max_severity = HealthStatus.HEALTHY
        dominant_msg = "All monitored components healthy"

        for comp in self._components.values():
            if _ROLLUP_PRIORITY.index(comp.status) > _ROLLUP_PRIORITY.index(max_severity):
                max_severity = comp.status
                dominant_msg = (
                    f"Component '{comp.name}' reports {comp.status.value}: {comp.message}"
                )

        return SystemHealthSnapshot(
            components=dict(self._components),
            overall_status=max_severity,
            message=dominant_msg,
        )
