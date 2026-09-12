"""
Unit tests for AlphaForge Crash Recovery State Store.
Verifies atomic file persistence, schema validation, and fail-closed handling
of corrupted or truncated recovery snapshots.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from alphaforge.core.exceptions import CorruptedStateError
from alphaforge.execution.enums import OrderState
from alphaforge.reconciliation.state_store import (
    AtomicStateStore,
    InMemoryStateStore,
    LocalOrderRecord,
    LocalPositionRecord,
    RecoverySnapshot,
)
from alphaforge.risk.enums import TradeSide


def test_atomic_state_store_roundtrip(tmp_path: Path) -> None:
    """Save valid RecoverySnapshot and load it cleanly from atomic state store."""
    state_file = tmp_path / "recovery_state.json"
    store = AtomicStateStore(state_file)
    now = datetime.now(UTC)

    order = LocalOrderRecord(
        order_id="ORD-1",
        client_order_id="AF-E-SNAP1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        filled_quantity=0,
        state=OrderState.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )
    pos = LocalPositionRecord(
        position_id="POS-1",
        symbol="NIFTY",
        side=TradeSide.LONG,
        quantity=50,
        average_price=Decimal("24500.00"),
        status="OPEN",
        is_protected=True,
    )
    snap = RecoverySnapshot(
        schema_version=1,
        orders={"AF-E-SNAP1": order},
        positions={"NIFTY": pos},
        reconciliation_gate_open=True,
        created_at=now,
    )

    store.save_snapshot(snap)
    loaded = store.load_snapshot()

    assert loaded is not None
    assert loaded.schema_version == 1
    assert "AF-E-SNAP1" in loaded.orders
    assert loaded.orders["AF-E-SNAP1"].symbol == "NIFTY"
    assert loaded.positions["NIFTY"].quantity == 50
    assert loaded.reconciliation_gate_open is True


def test_atomic_state_store_corrupted_json_fails_closed(tmp_path: Path) -> None:
    """Corrupted or malformed JSON file must raise CorruptedStateError."""
    state_file = tmp_path / "corrupt_state.json"
    state_file.write_text("{ this is not valid json", encoding="utf-8")

    store = AtomicStateStore(state_file)
    with pytest.raises(CorruptedStateError, match="Corrupted or schema-invalid"):
        store.load_snapshot()


def test_atomic_state_store_empty_file_fails_closed(tmp_path: Path) -> None:
    """Empty state file must raise CorruptedStateError."""
    state_file = tmp_path / "empty_state.json"
    state_file.write_text("", encoding="utf-8")

    store = AtomicStateStore(state_file)
    with pytest.raises(CorruptedStateError, match="empty"):
        store.load_snapshot()


def test_atomic_state_store_schema_invalid_fails_closed(tmp_path: Path) -> None:
    """JSON with missing mandatory fields must raise CorruptedStateError."""
    state_file = tmp_path / "invalid_schema.json"
    state_file.write_text('{"schema_version": 1, "orders": "not-a-dict"}', encoding="utf-8")

    store = AtomicStateStore(state_file)
    with pytest.raises(CorruptedStateError, match="Corrupted or schema-invalid"):
        store.load_snapshot()


def test_in_memory_state_store_roundtrip() -> None:
    """InMemoryStateStore save, load, and delete behavior."""
    store = InMemoryStateStore()
    assert store.load_snapshot() is None

    now = datetime.now(UTC)
    snap = RecoverySnapshot(
        schema_version=1,
        created_at=now,
    )
    store.save_snapshot(snap)
    assert store.load_snapshot() == snap

    store.delete()
    assert store.load_snapshot() is None
