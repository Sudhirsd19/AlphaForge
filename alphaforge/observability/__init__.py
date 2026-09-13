"""
AlphaForge Phase 14 Observability Package.

Provides diagnostic visibility into algorithmic trading execution across PAPER,
SHADOW, and future CONTROLLED LIVE modes without altering trading mathematics or domain semantics.
"""

from __future__ import annotations

from alphaforge.observability.context import TraceContext, trace_span
from alphaforge.observability.events import (
    DataEventType,
    FillEventType,
    ObservabilityCategory,
    ObservabilityEvent,
    ObservabilitySeverity,
    OrderEventType,
    PositionEventType,
    ReconciliationEventType,
    RecoveryEventType,
    RiskEventType,
    SecurityEventType,
    StrategyEventType,
    SystemEventType,
    compute_deterministic_event_id,
)
from alphaforge.observability.health import (
    ComponentHealth,
    HealthAggregator,
    HealthStatus,
    SystemHealthSnapshot,
)
from alphaforge.observability.hub import (
    OrderFSMObservabilityAdapter,
    ReconciliationObservabilityAdapter,
    get_global_dispatcher,
    get_global_health,
    get_global_metrics,
    observe_event,
    observe_event_safely,
    reset_observability_hub,
    set_global_dispatcher,
)
from alphaforge.observability.logger import get_observability_logger
from alphaforge.observability.metrics import MetricsRegistry
from alphaforge.observability.sinks import (
    AbstractObservabilitySink,
    InMemoryObservabilitySink,
    JsonlObservabilitySink,
    SafeObservabilityDispatcher,
)

__all__ = [
    # Event models & taxonomy
    "ObservabilitySeverity",
    "ObservabilityCategory",
    "SystemEventType",
    "DataEventType",
    "StrategyEventType",
    "RiskEventType",
    "SecurityEventType",
    "OrderEventType",
    "FillEventType",
    "PositionEventType",
    "ReconciliationEventType",
    "RecoveryEventType",
    "ObservabilityEvent",
    "compute_deterministic_event_id",
    # Trace context
    "TraceContext",
    "trace_span",
    # Sinks & Dispatcher
    "AbstractObservabilitySink",
    "InMemoryObservabilitySink",
    "JsonlObservabilitySink",
    "SafeObservabilityDispatcher",
    # Metrics
    "MetricsRegistry",
    # Health
    "HealthStatus",
    "ComponentHealth",
    "SystemHealthSnapshot",
    "HealthAggregator",
    # Logging
    "get_observability_logger",
    # Hub & Adapters
    "get_global_dispatcher",
    "set_global_dispatcher",
    "get_global_metrics",
    "get_global_health",
    "reset_observability_hub",
    "observe_event",
    "observe_event_safely",
    "OrderFSMObservabilityAdapter",
    "ReconciliationObservabilityAdapter",
]
