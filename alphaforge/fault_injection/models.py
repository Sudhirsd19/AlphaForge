"""
AlphaForge Fault Injection Data Models.
Machine-readable failure scenario definitions with deterministic trigger controls.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FaultPoint(StrEnum):
    """Deterministic injection points in the AlphaForge execution pipeline."""

    ORDER_SUBMIT_BEFORE = "ORDER_SUBMIT_BEFORE"
    ORDER_SUBMIT_AFTER_ACCEPT = "ORDER_SUBMIT_AFTER_ACCEPT"
    FILL_RESPONSE = "FILL_RESPONSE"
    CANCEL_RESPONSE = "CANCEL_RESPONSE"
    REJECT_RESPONSE = "REJECT_RESPONSE"
    LEDGER_APPEND = "LEDGER_APPEND"
    STORAGE_WRITE = "STORAGE_WRITE"
    STORAGE_READ = "STORAGE_READ"
    CHECKPOINT_SAVE = "CHECKPOINT_SAVE"
    CHECKPOINT_LOAD = "CHECKPOINT_LOAD"
    RECONCILIATION_QUERY = "RECONCILIATION_QUERY"
    DATA_INGESTION = "DATA_INGESTION"
    RISK_EVALUATION = "RISK_EVALUATION"


class FaultAction(StrEnum):
    """Deterministic fault behaviors that can be injected."""

    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    CORRUPT_PAYLOAD = "CORRUPT_PAYLOAD"
    DROP_EVENT = "DROP_EVENT"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    REORDER_EVENTS = "REORDER_EVENTS"
    PROCESS_CRASH = "PROCESS_CRASH"
    PARTIAL_WRITE = "PARTIAL_WRITE"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    CORRUPT_HASH = "CORRUPT_HASH"
    CORRUPT_SEQUENCE = "CORRUPT_SEQUENCE"


class FaultScenario(BaseModel):
    """Machine-readable failure scenario definition."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(description="Unique scenario identifier (e.g. 'O2', 'L3')")
    fault_point: FaultPoint = Field(description="Injection point in pipeline")
    fault_action: FaultAction = Field(description="Type of failure injected")
    trigger_count: int = Field(
        default=1,
        ge=1,
        description="Fire after N-th occurrence at the fault point",
    )
    description: str = Field(description="Human-readable scenario description")
    expected_outcome: str = Field(description="Expected system behavior after fault")
    safety_invariant: str = Field(description="Safety property that must hold")


class FaultResult(BaseModel):
    """Deterministic result of a fault injection test."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scenario_id: str = Field(description="Tested scenario identifier")
    fault_point: FaultPoint = Field(description="Where fault was injected")
    injected_at: str = Field(description="Specific entity/operation affected")
    affected_entity: str = Field(description="Domain entity impacted")
    expected_behavior: str = Field(description="What should happen")
    actual_behavior: str = Field(description="What actually happened")
    recovery_result: str = Field(
        description="Recovery outcome: SAFE_RECOVERY | FAIL_CLOSED | UNSAFE"
    )
    passed: bool = Field(description="Whether the test passed")


class ProcessCrashError(Exception):
    """Simulated process crash for fault injection testing."""

    def __init__(self, fault_point: str, message: str = "") -> None:
        self.fault_point = fault_point
        super().__init__(message or f"Simulated process crash at {fault_point}")
