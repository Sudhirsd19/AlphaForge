"""
AlphaForge Fault Injection Safety Invariants.
Reusable assertion functions that verify critical safety properties
hold after fault injection and recovery.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from alphaforge.execution.state_machine import TERMINAL_STATES
from alphaforge.ledger.models import AuditEvent


def assert_no_duplicate_orders(
    order_ids: Sequence[str],
) -> None:
    """Verify no duplicate order IDs exist."""
    seen: set[str] = set()
    for oid in order_ids:
        if oid in seen:
            raise AssertionError(f"Duplicate order detected: {oid}")
        seen.add(oid)


def assert_no_duplicate_fills(
    fill_records: Sequence[dict[str, Any]],
) -> None:
    """Verify no duplicate fill events for the same order."""
    seen: set[tuple[str, int]] = set()
    for fill in fill_records:
        key = (
            str(fill.get("order_id", "")),
            int(fill.get("sequence", 0)),
        )
        if key in seen:
            raise AssertionError(f"Duplicate fill detected: order={key[0]}, seq={key[1]}")
        seen.add(key)


def assert_no_phantom_positions(
    position_ids: Sequence[str],
    valid_order_ids: Sequence[str],
) -> None:
    """Verify every position traces back to a valid order."""
    valid_set = set(valid_order_ids)
    for pid in position_ids:
        if pid not in valid_set:
            raise AssertionError(
                f"Phantom position detected: {pid} has no corresponding valid order"
            )


def assert_no_double_pnl(
    trade_pnls: Sequence[tuple[str, Decimal]],
) -> None:
    """Verify each trade ID appears at most once in PnL records."""
    seen: set[str] = set()
    for trade_id, _pnl in trade_pnls:
        if trade_id in seen:
            raise AssertionError(f"Double PnL detected for trade: {trade_id}")
        seen.add(trade_id)


def assert_valid_fsm_history(
    transitions: Sequence[tuple[str, str]],
    allowed: dict[str, frozenset[str]] | None = None,
) -> None:
    """Verify every (from_state, to_state) pair is legal."""
    from alphaforge.execution.state_machine import (
        ALLOWED_TRANSITIONS,
    )

    matrix = allowed or {
        k.value: frozenset(s.value for s in v) for k, v in ALLOWED_TRANSITIONS.items()
    }
    for from_s, to_s in transitions:
        allowed_targets = matrix.get(from_s, frozenset())
        if to_s not in allowed_targets:
            raise AssertionError(f"Illegal FSM transition: {from_s} -> {to_s}")


def assert_hash_chain_intact(events: Sequence[AuditEvent]) -> None:
    """Verify the cryptographic hash chain is unbroken."""
    for i in range(1, len(events)):
        prev = events[i - 1]
        curr = events[i]
        if curr.previous_event_hash != prev.event_hash:
            raise AssertionError(
                f"Hash chain broken at sequence "
                f"{curr.sequence_number}: "
                f"prev_hash={curr.previous_event_hash} != "
                f"expected={prev.event_hash}"
            )
        # Verify sequence contiguity
        if curr.sequence_number != prev.sequence_number + 1:
            raise AssertionError(f"Sequence gap: {prev.sequence_number} -> {curr.sequence_number}")


def assert_risk_limits_enforced(
    open_trade_count: int,
    max_open_trades: int,
    reserved_risk: Decimal,
    max_portfolio_risk_amount: Decimal,
) -> None:
    """Verify risk limits have not been silently bypassed."""
    if open_trade_count > max_open_trades:
        raise AssertionError(
            f"Risk limit bypassed: open_trades={open_trade_count} > max={max_open_trades}"
        )
    if reserved_risk > max_portfolio_risk_amount:
        raise AssertionError(
            f"Risk limit bypassed: reserved_risk={reserved_risk} > max={max_portfolio_risk_amount}"
        )


def assert_no_fabricated_data(
    data_source: str,
    values: Sequence[Any],
) -> None:
    """Verify no values were fabricated (e.g. 'UNKNOWN', None defaults)."""
    for val in values:
        if val in ("UNKNOWN", "FABRICATED", None):
            raise AssertionError(f"Fabricated data detected in {data_source}: {val}")


def assert_terminal_state_immutable(
    state: str,
    attempted_target: str,
) -> None:
    """Verify terminal states cannot be resurrected."""
    terminal_values = {s.value for s in TERMINAL_STATES}
    if state in terminal_values:
        raise AssertionError(
            f"Terminal state resurrection attempted: {state} -> {attempted_target}"
        )


def assert_sequence_contiguous(
    sequence_numbers: Sequence[int],
) -> None:
    """Verify sequence numbers are strictly contiguous (1, 2, 3, ...)."""
    for i in range(1, len(sequence_numbers)):
        if sequence_numbers[i] != sequence_numbers[i - 1] + 1:
            raise AssertionError(
                f"Sequence gap: {sequence_numbers[i - 1]} -> {sequence_numbers[i]}"
            )
