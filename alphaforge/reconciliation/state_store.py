"""
AlphaForge Crash Recovery State Persistence.
Provides atomic, crash-safe local persistence for open orders and positions.
Uses atomic file replacement (temp file + flush + fsync + os.replace) to eliminate partial writes.
Fails closed with CorruptedStateError upon reading malformed or schema-invalid snapshots.
"""

import json
import os
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from alphaforge.core.exceptions import CorruptedStateError, OrderValidationError
from alphaforge.execution.enums import OrderState
from alphaforge.risk.enums import TradeSide


class LocalOrderRecord(BaseModel):
    """
    Immutable snapshot record of an active local order persisted for crash recovery.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: str = Field(description="Internal unique order identifier")
    client_order_id: str = Field(description="Deterministic client order identifier")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="Position trade side: LONG | SHORT")
    quantity: int = Field(gt=0, description="Total order quantity")
    filled_quantity: int = Field(default=0, ge=0, description="Executed quantity")
    state: OrderState = Field(description="Phase 7 lifecycle state")
    signal_id: str | None = Field(default=None, description="Associated signal ID")
    position_id: str | None = Field(default=None, description="Associated position ID")
    is_protection_confirmed: bool = Field(
        default=False, description="Whether resting stop protection is confirmed"
    )
    protection_order_id: str | None = Field(default=None, description="Resting protection order ID")
    filled_at: datetime | None = Field(default=None, description="UTC fill timestamp")
    created_at: datetime = Field(description="UTC creation timestamp")
    updated_at: datetime = Field(description="UTC update timestamp")

    @field_validator("order_id", "client_order_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("created_at", "updated_at", "filled_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"Timestamp must be timezone-aware UTC: {v}")
        return v


class LocalPositionRecord(BaseModel):
    """
    Immutable snapshot record of an active local position persisted for crash recovery.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    position_id: str = Field(description="Unique position identifier")
    symbol: str = Field(description="Trading symbol")
    side: TradeSide = Field(description="Position trade side: LONG | SHORT")
    quantity: int = Field(ge=0, description="Net open position quantity (0 = flat)")
    average_price: Decimal | None = Field(default=None, description="Average entry price")
    status: str = Field(default="OPEN", description="Position status: OPEN | FLAT")
    is_protected: bool = Field(
        default=False, description="Whether resting stop protection is confirmed"
    )
    protection_order_id: str | None = Field(
        default=None, description="Confirmed resting stop-loss order ID"
    )

    @field_validator("position_id", "symbol")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean


class RecoverySnapshot(BaseModel):
    """
    Immutable root recovery snapshot container persisted to disk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: int = Field(default=1, description="Snapshot schema revision")
    orders: dict[str, LocalOrderRecord] = Field(
        default_factory=dict, description="Active orders indexed by client_order_id"
    )
    positions: dict[str, LocalPositionRecord] = Field(
        default_factory=dict, description="Active positions indexed by symbol"
    )
    reconciliation_gate_open: bool = Field(
        default=False, description="Persisted reconciliation gate state"
    )
    created_at: datetime = Field(description="UTC snapshot generation timestamp")

    @field_validator("created_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise OrderValidationError(f"Timestamp must be timezone-aware UTC: {v}")
        return v


class AtomicStateStore:
    """
    Crash-safe, atomic file persistence store for RecoverySnapshots.
    Uses temp-file + fsync + atomic rename protocol to prevent partial write corruption.
    """

    def __init__(self, file_path: Path | str) -> None:
        self._file_path = Path(file_path).resolve()
        self._lock = threading.RLock()

    @property
    def file_path(self) -> Path:
        return self._file_path

    def save_snapshot(self, snapshot: RecoverySnapshot) -> None:
        """
        Atomically persist a validated RecoverySnapshot to disk.
        Protocol:
        1. Acquire lock.
        2. Serialize to JSON.
        3. Write to .tmp sibling file.
        4. Flush and fsync to guarantee disk write.
        5. Atomically replace target file.
        """
        with self._lock:
            # Ensure parent directory exists
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._file_path.with_suffix(".tmp")
            payload = snapshot.model_dump_json(indent=2)

            with temp_path.open("w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename / replace
            temp_path.replace(self._file_path)

    def load_snapshot(self) -> RecoverySnapshot | None:
        """
        Load and validate the RecoverySnapshot from disk.
        Returns None if no persisted snapshot exists.
        Raises CorruptedStateError if snapshot is malformed, truncated, or schema-invalid.
        """
        with self._lock:
            if not self._file_path.exists():
                return None

            try:
                with self._file_path.open(encoding="utf-8") as f:
                    content = f.read()

                if not content.strip():
                    raise CorruptedStateError(
                        f"Persisted recovery snapshot is empty: {self._file_path}"
                    )

                return RecoverySnapshot.model_validate_json(content)
            except (json.JSONDecodeError, ValidationError) as e:
                raise CorruptedStateError(
                    f"Corrupted or schema-invalid recovery snapshot at '{self._file_path}': {e}"
                ) from e
            except Exception as e:
                raise CorruptedStateError(
                    f"Failed to read recovery snapshot at '{self._file_path}': {e}"
                ) from e

    def delete(self) -> None:
        """Remove persisted state file if exists."""
        with self._lock:
            if self._file_path.exists():
                self._file_path.unlink()
            temp_path = self._file_path.with_suffix(".tmp")
            if temp_path.exists():
                temp_path.unlink()


class InMemoryStateStore:
    """
    In-memory state store providing the same interface as AtomicStateStore.
    Used for in-memory testing and high-speed simulation.
    """

    def __init__(self) -> None:
        self._snapshot: RecoverySnapshot | None = None
        self._lock = threading.RLock()

    def save_snapshot(self, snapshot: RecoverySnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def load_snapshot(self) -> RecoverySnapshot | None:
        with self._lock:
            return self._snapshot

    def delete(self) -> None:
        with self._lock:
            self._snapshot = None
