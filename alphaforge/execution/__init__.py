"""
AlphaForge Execution & Order Lifecycle Module.
Provides the 17-state deterministic Order State Machine and the
Emergency Unprotected Position Protocol.
"""

from alphaforge.execution.enums import (
    ExecutionReasonCode,
    OrderEvent,
    OrderSide,
    OrderState,
    WatchdogStatus,
)
from alphaforge.execution.models import (
    EmergencyExitCommand,
    Order,
    TransitionEvent,
    TransitionResult,
    WatchdogDecision,
)
from alphaforge.execution.protection_watchdog import (
    ProtectionConfig,
    ProtectionWatchdog,
)
from alphaforge.execution.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    OrderStateMachine,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "EmergencyExitCommand",
    "ExecutionReasonCode",
    "Order",
    "OrderEvent",
    "OrderSide",
    "OrderState",
    "OrderStateMachine",
    "ProtectionConfig",
    "ProtectionWatchdog",
    "TransitionEvent",
    "TransitionResult",
    "WatchdogDecision",
    "WatchdogStatus",
]
