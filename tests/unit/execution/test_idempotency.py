"""
Unit tests for AlphaForge Idempotency & Order Identity.
Verifies deterministic client_order_id generation, execution attempt tracking,
and collision detection in IdempotencyRegistry.
"""

import threading

import pytest

from alphaforge.core.exceptions import IdempotencyCollisionError, OrderValidationError
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_attempt_id,
    generate_client_order_id,
)
from alphaforge.risk.enums import TradeSide


def test_deterministic_client_order_id_generation() -> None:
    """Same canonical intent must always produce the exact same client_order_id."""
    id1 = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-100")
    id2 = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-100")
    assert id1 == id2
    assert id1.startswith("AF-E-")
    assert len(id1) == 29  # AF-E- + 24 hex chars <= 32


def test_client_order_id_differs_on_intent_changes() -> None:
    """Changing any canonical input must alter the generated client_order_id."""
    base = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-100")

    diff_sig = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-200")
    assert base != diff_sig

    diff_role = generate_client_order_id("STRAT-1", "1.0.0", "NIFTY", OrderRole.STOP, "SIG-100")
    assert base != diff_role
    assert diff_role.startswith("AF-S-")

    diff_sym = generate_client_order_id("STRAT-1", "1.0.0", "BANKNIFTY", OrderRole.ENTRY, "SIG-100")
    assert base != diff_sym

    diff_strat = generate_client_order_id("STRAT-2", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-100")
    assert base != diff_strat

    diff_ver = generate_client_order_id("STRAT-1", "2.0.0", "NIFTY", OrderRole.ENTRY, "SIG-100")
    assert base != diff_ver


def test_client_order_id_normalizes_case_and_whitespace() -> None:
    """Leading/trailing whitespace and lowercase inputs normalize deterministically."""
    id1 = generate_client_order_id("strat-1", "1.0.0", "nifty", OrderRole.ENTRY, "sig-100")
    id2 = generate_client_order_id(" STRAT-1 ", " 1.0.0 ", " NIFTY ", OrderRole.ENTRY, " SIG-100 ")
    assert id1 == id2


def test_client_order_id_rejects_empty_inputs() -> None:
    """Empty or whitespace-only inputs must fail closed with OrderValidationError."""
    with pytest.raises(OrderValidationError):
        generate_client_order_id("", "1.0.0", "NIFTY", OrderRole.ENTRY, "SIG-1")
    with pytest.raises(OrderValidationError):
        generate_client_order_id("STRAT", "", "NIFTY", OrderRole.ENTRY, "SIG-1")
    with pytest.raises(OrderValidationError):
        generate_client_order_id("STRAT", "1.0.0", "  ", OrderRole.ENTRY, "SIG-1")
    with pytest.raises(OrderValidationError):
        generate_client_order_id("STRAT", "1.0.0", "NIFTY", OrderRole.ENTRY, "")


def test_generate_attempt_id() -> None:
    """Attempt ID must be deterministic and increment monotonically."""
    att1 = generate_attempt_id("AF-E-12345", 1)
    att2 = generate_attempt_id("AF-E-12345", 2)
    assert att1 == "AF-E-12345-ATTEMPT-1"
    assert att2 == "AF-E-12345-ATTEMPT-2"

    with pytest.raises(OrderValidationError):
        generate_attempt_id("AF-E-12345", 0)
    with pytest.raises(OrderValidationError):
        generate_attempt_id("AF-E-12345", -1)


def test_idempotency_registry_duplicate_noop() -> None:
    """Duplicate registration of identical intent returns existing record (NO-OP)."""
    registry = IdempotencyRegistry()
    intent = OrderIntent(
        client_order_id="AF-E-1111",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-1",
        side=TradeSide.LONG,
        quantity=50,
    )
    reg1 = registry.register(intent)
    reg2 = registry.register(intent)

    assert reg1 == intent
    assert reg2 == intent
    assert len(registry.all_intents()) == 1


def test_idempotency_registry_collision_failure() -> None:
    """Registering conflicting intent for same client_order_id raises collision error."""
    registry = IdempotencyRegistry()
    intent1 = OrderIntent(
        client_order_id="AF-E-COLLIDE",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-1",
        side=TradeSide.LONG,
        quantity=50,
    )
    registry.register(intent1)

    # Conflicting quantity
    intent_conflict_qty = OrderIntent(
        client_order_id="AF-E-COLLIDE",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-1",
        side=TradeSide.LONG,
        quantity=100,  # Conflict!
    )
    with pytest.raises(IdempotencyCollisionError, match="Idempotency collision detected"):
        registry.register(intent_conflict_qty)

    # Conflicting side
    intent_conflict_side = OrderIntent(
        client_order_id="AF-E-COLLIDE",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-1",
        side=TradeSide.SHORT,  # Conflict!
        quantity=50,
    )
    with pytest.raises(IdempotencyCollisionError, match="Idempotency collision detected"):
        registry.register(intent_conflict_side)


def test_idempotency_registry_thread_safety() -> None:
    """Multiple threads registering identical intent concurrently succeed without corruption."""
    registry = IdempotencyRegistry()
    intent = OrderIntent(
        client_order_id="AF-E-THREAD",
        strategy_id="STRAT-1",
        strategy_version="1.0.0",
        symbol="NIFTY",
        role=OrderRole.ENTRY,
        signal_id="SIG-1",
        side=TradeSide.LONG,
        quantity=25,
    )

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(20):
                registry.register(intent)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(registry.all_intents()) == 1
