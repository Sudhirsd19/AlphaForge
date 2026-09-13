"""
AlphaForge Fault Injection Framework.
Provides deterministic, isolated failure simulation for Phase 12 testing.
"""

from alphaforge.fault_injection.models import (
    FaultAction,
    FaultPoint,
    FaultResult,
    FaultScenario,
)

__all__ = [
    "FaultAction",
    "FaultPoint",
    "FaultResult",
    "FaultScenario",
]
