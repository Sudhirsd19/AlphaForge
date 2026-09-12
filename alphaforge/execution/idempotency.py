"""
AlphaForge Deterministic Idempotency & Order Identity Module.
Provides collision-resistant client_order_id generation, execution attempt tracking,
and an in-memory thread-safe idempotency registry to prevent duplicate orders.
"""

import hashlib
import threading
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphaforge.core.exceptions import IdempotencyCollisionError, OrderValidationError
from alphaforge.risk.enums import TradeSide


class OrderRole(StrEnum):
    """
    Authoritative order roles defining an order's lifecycle purpose.
    Forms part of the canonical client_order_id identity.
    """

    ENTRY = "ENTRY"
    STOP = "STOP"
    EXIT = "EXIT"


def generate_client_order_id(
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    role: OrderRole,
    signal_id: str,
) -> str:
    """
    Generate an authoritative, deterministic client_order_id from canonical order intent.

    Contract:
    - Same canonical intent ALWAYS produces the exact same client_order_id.
    - Different intent produces a different client_order_id.
    - Independent of timestamps, process IDs, hostnames, and random UUIDs.
    - Uses 24 hex characters of SHA-256 (2^96 collision resistance, total length 29 chars).
    """
    clean_strategy = strategy_id.strip().upper()
    if not clean_strategy:
        raise OrderValidationError("strategy_id must be non-empty and uppercase")

    clean_version = strategy_version.strip().upper()
    if not clean_version:
        raise OrderValidationError("strategy_version must be non-empty and uppercase")

    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        raise OrderValidationError("symbol must be non-empty and uppercase")

    clean_signal = signal_id.strip().upper()
    if not clean_signal:
        raise OrderValidationError("signal_id must be non-empty and uppercase")

    canonical_raw = f"{clean_strategy}|{clean_version}|{clean_symbol}|{role.value}|{clean_signal}"
    digest = hashlib.sha256(canonical_raw.encode("utf-8")).hexdigest().upper()

    # Total length: 3 ("AF-") + 1 (Role Initial) + 1 ("-") + 24 (Hex digest) = 29 characters <= 32
    role_initial = role.value[0]
    return f"AF-{role_initial}-{digest[:24]}"


def generate_attempt_id(client_order_id: str, attempt_number: int) -> str:
    """
    Generate a deterministic execution attempt identifier.
    Separates logical order identity from individual physical execution attempts.
    """
    clean_id = client_order_id.strip().upper()
    if not clean_id:
        raise OrderValidationError("client_order_id must be non-empty")
    if attempt_number <= 0:
        raise OrderValidationError(f"attempt_number must be >= 1, got {attempt_number}")
    return f"{clean_id}-ATTEMPT-{attempt_number}"


class OrderIntent(BaseModel):
    """
    Immutable representation of canonical order intent registered for idempotency.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    client_order_id: str = Field(description="Deterministic client order identifier")
    strategy_id: str = Field(description="Associated strategy identifier")
    strategy_version: str = Field(description="Associated strategy semantic version")
    symbol: str = Field(description="Target instrument symbol")
    role: OrderRole = Field(description="Order role: ENTRY | STOP | EXIT")
    signal_id: str = Field(description="Associated strategy signal identifier")
    side: TradeSide = Field(description="Position trade side: LONG | SHORT")
    quantity: int = Field(gt=0, description="Intended order quantity")

    @field_validator("client_order_id", "strategy_id", "strategy_version", "symbol", "signal_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise OrderValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean


class IdempotencyRegistry:
    """
    Thread-safe registry mapping client_order_id to canonical OrderIntent.
    Enforces strict duplicate idempotency and fails closed on intent collisions.
    """

    def __init__(self) -> None:
        self._intents: dict[str, OrderIntent] = {}
        self._lock: threading.RLock = threading.RLock()

    def register(self, intent: OrderIntent) -> OrderIntent:
        """
        Register an order intent.
        - If client_order_id is new: registers and returns intent.
        - If client_order_id exists and intent matches: returns existing intent (idempotent NO-OP).
        - If client_order_id exists with conflicting intent: raises IdempotencyCollisionError.
        """
        with self._lock:
            existing = self._intents.get(intent.client_order_id)
            if existing is not None:
                if existing == intent:
                    return existing
                msg = (
                    f"Idempotency collision detected for client_order_id "
                    f"'{intent.client_order_id}'. Existing intent {existing} "
                    f"conflicts with new intent {intent}."
                )
                raise IdempotencyCollisionError(msg)
            self._intents[intent.client_order_id] = intent
            return intent

    def get(self, client_order_id: str) -> OrderIntent | None:
        """Retrieve registered intent for client_order_id."""
        with self._lock:
            return self._intents.get(client_order_id.strip().upper())

    def contains(self, client_order_id: str) -> bool:
        """Query if client_order_id is registered."""
        with self._lock:
            return client_order_id.strip().upper() in self._intents

    def clear(self) -> None:
        """Clear registry."""
        with self._lock:
            self._intents.clear()

    def all_intents(self) -> tuple[OrderIntent, ...]:
        """Return defensive immutable snapshot of all registered intents."""
        with self._lock:
            return tuple(self._intents.values())
