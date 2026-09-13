"""
AlphaForge Extended Real-Market Shadow Validation & Certification Enums (Phase 17).
Defines authoritative enumerations for data anomalies, fill execution modes,
lifecycle states, data source tiers, and multi-level certification statuses.
"""

from enum import StrEnum


class MarketDataAnomalyType(StrEnum):
    """Types of market data anomalies detected during live/shadow streaming."""

    LIVE = "LIVE"
    STALE = "STALE"
    MISSING = "MISSING"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    INVALID = "INVALID"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTED = "RECONNECTED"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    SEQUENCE_REVERSAL = "SEQUENCE_REVERSAL"
    UNVERIFIED_PROVENANCE = "UNVERIFIED_PROVENANCE"


class FillExecutionType(StrEnum):
    """Realistic shadow fill execution classification."""

    IMMEDIATE_FULL = "IMMEDIATE_FULL"
    PARTIAL_REMAINING = "PARTIAL_REMAINING"
    PARTIAL_CANCEL = "PARTIAL_CANCEL"
    DELAYED = "DELAYED"
    NO_FILL = "NO_FILL"
    RAPID_MOVEMENT = "RAPID_MOVEMENT"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    PARTIAL_TP = "PARTIAL_TP"
    PARTIAL_SL = "PARTIAL_SL"


class ProcessLifecycleState(StrEnum):
    """Operational lifecycle state of trading process for crash recovery testing."""

    NO_POSITION = "NO_POSITION"
    PENDING_SIGNAL = "PENDING_SIGNAL"
    SIMULATED_ENTRY = "SIMULATED_ENTRY"
    PARTIAL_ENTRY = "PARTIAL_ENTRY"
    OPEN_POSITION = "OPEN_POSITION"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    TP_STATE = "TP_STATE"
    SL_STATE = "SL_STATE"
    EMERGENCY_CLOSE = "EMERGENCY_CLOSE"


class DataSourceType(StrEnum):
    """Authoritative classification of market data stream source."""

    SYNTHETIC = "SYNTHETIC"
    HISTORICAL = "HISTORICAL"
    REPLAY = "REPLAY"
    MOCK = "MOCK"
    REAL_MARKET_SHADOW = "REAL_MARKET_SHADOW"
    UNKNOWN = "UNKNOWN"


class CertificationLevelStatus(StrEnum):
    """Status of an individual certification level."""

    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    PENDING = "PENDING"


class CertificationVerdict(StrEnum):
    """Final system certification verdict."""

    PHASE_17_PASS = "PHASE 17 PASS"  # noqa: S105
    PHASE_17_FAIL = "PHASE 17 FAIL"
    PHASE_17_BLOCKED = "PHASE 17 BLOCKED"
