"""
Unit tests for AlphaForge Reconciliation Gate.
Verifies entry blocking, precondition checks for gate opening,
and fail-closed locking upon any reconciliation failure.
"""

from datetime import UTC, datetime

import pytest

from alphaforge.core.exceptions import ReconciliationError
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.reconciliation.models import (
    ReconciliationReasonCode,
    ReconciliationResult,
    ReconciliationStatus,
)


def test_reconciliation_gate_initially_closed() -> None:
    """Gate must start in a closed, fail-closed state."""
    gate = ReconciliationGate()
    assert not gate.is_open
    assert not gate.can_accept_new_entries()
    assert "reconciliation pending" in gate.reason.lower()


def test_reconciliation_gate_opens_on_matched_result() -> None:
    """Gate opens cleanly when provided with a verified MATCHED ReconciliationResult."""
    gate = ReconciliationGate()
    now = datetime.now(UTC)

    res = ReconciliationResult(
        reconciliation_id="REC-001",
        timestamp=now,
        status=ReconciliationStatus.MATCHED,
        local_order_count=1,
        broker_order_count=1,
        local_position_count=0,
        broker_position_count=0,
        matched_count=1,
        mismatch_count=0,
        unknown_count=0,
        new_entries_allowed=True,
        manual_escalation_required=False,
        reason_code=ReconciliationReasonCode.MATCHED,
    )
    gate.open(res)
    assert gate.is_open
    assert gate.can_accept_new_entries()
    assert "verified matched" in gate.reason


def test_reconciliation_gate_rejects_non_matched_statuses() -> None:
    """Gate raises ReconciliationError and remains closed on MISMATCH, ESCALATED, or FAILED."""
    gate = ReconciliationGate()
    now = datetime.now(UTC)

    # Status MISMATCH
    res_mismatch = ReconciliationResult(
        reconciliation_id="REC-002",
        timestamp=now,
        status=ReconciliationStatus.MISMATCH,
        local_order_count=1,
        broker_order_count=1,
        local_position_count=1,
        broker_position_count=1,
        matched_count=0,
        mismatch_count=1,
        unknown_count=0,
        new_entries_allowed=False,
        manual_escalation_required=True,
        reason_code=ReconciliationReasonCode.POSITION_MISMATCH,
    )
    with pytest.raises(ReconciliationError, match="status is 'MISMATCH'"):
        gate.open(res_mismatch)
    assert not gate.can_accept_new_entries()

    # Status FAILED
    res_failed = ReconciliationResult(
        reconciliation_id="REC-003",
        timestamp=now,
        status=ReconciliationStatus.FAILED,
        local_order_count=0,
        broker_order_count=0,
        local_position_count=0,
        broker_position_count=0,
        matched_count=0,
        mismatch_count=0,
        unknown_count=0,
        new_entries_allowed=False,
        manual_escalation_required=True,
        reason_code=ReconciliationReasonCode.BROKER_UNAVAILABLE,
    )
    with pytest.raises(ReconciliationError, match="status is 'FAILED'"):
        gate.open(res_failed)
    assert not gate.can_accept_new_entries()


def test_reconciliation_gate_close() -> None:
    """Calling close locks the gate immediately."""
    gate = ReconciliationGate(initially_open=True)
    assert gate.can_accept_new_entries()

    gate.close("Emergency hazard detected")
    assert not gate.is_open
    assert not gate.can_accept_new_entries()
    assert "Emergency hazard detected" in gate.reason
