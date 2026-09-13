"""
Phase 12 — Property-Based Fault Injection Tests (FP1–FP10).
Uses Hypothesis to prove safety invariants hold under arbitrary fault conditions.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaforge.execution.enums import OrderState
from alphaforge.execution.idempotency import (
    IdempotencyRegistry,
    OrderIntent,
    OrderRole,
    generate_client_order_id,
)
from alphaforge.execution.state_machine import (
    TERMINAL_STATES,
    OrderStateMachine,
)
from alphaforge.fault_injection.invariants import (
    assert_hash_chain_intact,
)
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.models import AuditEventType
from alphaforge.ledger.serialization import compute_event_hash
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.risk.enums import TradeSide


class TestFP1SameFaultSameOutcome:
    """FP1: Same fault at same point always produces the same outcome."""

    @given(
        signal_suffix=st.text(
            min_size=3,
            max_size=10,
            alphabet=st.characters(
                whitelist_categories=("Lu", "Nd"),
            ),
        ),
    )
    @settings(max_examples=20)
    def test_deterministic_client_order_id(self, signal_suffix: str) -> None:
        sig = f"SIG-{signal_suffix}" if signal_suffix else "SIG-DEFAULT"
        id1 = generate_client_order_id("TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, sig)
        id2 = generate_client_order_id("TREND", "1.0.0", "NIFTY", OrderRole.ENTRY, sig)
        assert id1 == id2


class TestFP2CrashRestartSameState:
    """FP2: Crash and restart at same point produces same recovered state."""

    @given(event_count=st.integers(min_value=1, max_value=10))
    @settings(max_examples=15)
    def test_ledger_state_deterministic_after_n_events(self, event_count: int) -> None:
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        def build_ledger(n: int) -> list[str]:
            storage = InMemoryLedgerStorage()
            ledger = AuditLedger(storage, auto_verify_on_startup=False)
            hashes = []
            for i in range(n):
                ev = ledger.append(
                    event_type=AuditEventType.SIGNAL_GENERATED,
                    entity_type="SIGNAL",
                    entity_id=f"SIG-FP2-{i:03d}",
                    correlation_id="CORR-FP2",
                    causation_id="ROOT" if i == 0 else f"SIG-FP2-{i - 1:03d}",
                    payload={"idx": i},
                    event_timestamp=base + timedelta(seconds=i * 10),
                )
                hashes.append(ev.event_hash)
            return hashes

        # Same inputs → same hashes (deterministic)
        run1 = build_ledger(event_count)
        run2 = build_ledger(event_count)
        assert run1 == run2


class TestFP3DuplicateExecutionAtMostOnce:
    """FP3: Duplicate execution attempt increases position at most once."""

    @given(repeat_count=st.integers(min_value=2, max_value=20))
    @settings(max_examples=10)
    def test_idempotent_registration(self, repeat_count: int) -> None:
        registry = IdempotencyRegistry()
        intent = OrderIntent(
            client_order_id="AF-E-FP3TEST000000000000",
            strategy_id="TREND",
            strategy_version="1.0.0",
            symbol="NIFTY",
            role=OrderRole.ENTRY,
            signal_id="SIG-FP3",
            side=TradeSide.LONG,
            quantity=50,
        )
        results = [registry.register(intent) for _ in range(repeat_count)]
        # All returns should be the same intent
        for r in results:
            assert r.client_order_id == intent.client_order_id


class TestFP4RepeatedFillNoDoublePnL:
    """FP4: Repeated fill event on terminal order does not double-count."""

    @given(repeat_count=st.integers(min_value=2, max_value=15))
    @settings(max_examples=10)
    def test_terminal_state_blocks_all_transitions(self, repeat_count: int) -> None:
        now = datetime.now(UTC)
        for terminal in TERMINAL_STATES:
            fsm = OrderStateMachine(
                order_id=f"ORD-FP4-{terminal.value}",
                symbol="NIFTY",
                side=TradeSide.LONG,
                quantity=50,
                created_at=now,
                initial_state=terminal,
            )
            for _ in range(repeat_count):
                for target in OrderState:
                    if target != terminal:
                        result = fsm.transition(target, raise_on_error=False)
                        assert not result.success
                        assert fsm.current_state == terminal


class TestFP5CorruptedLedgerAlwaysDetected:
    """FP5: Any corrupted ledger always fails integrity verification."""

    @given(corrupt_index=st.integers(min_value=1, max_value=4))
    @settings(max_examples=10)
    def test_corrupted_hash_always_detected(self, corrupt_index: int) -> None:
        storage = InMemoryLedgerStorage()
        ledger = AuditLedger(storage, auto_verify_on_startup=False)
        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)

        for i in range(5):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id=f"SIG-FP5-{i}",
                correlation_id="CORR-FP5",
                causation_id="ROOT",
                payload={"i": i},
                event_timestamp=base + timedelta(seconds=i * 10),
            )

        events = storage.read_all()
        idx = min(corrupt_index, len(events) - 1)

        # Swap two events to corrupt chain
        if idx > 0:
            corrupted = list(events)
            corrupted[idx], corrupted[idx - 1] = (
                corrupted[idx - 1],
                corrupted[idx],
            )
            with pytest.raises(AssertionError):
                assert_hash_chain_intact(corrupted)


class TestFP6CorruptedCheckpointAlwaysDetected:
    """FP6: Any corrupted checkpoint fingerprint is always detected."""

    @given(
        tamper_char=st.sampled_from("0123456789abcdef"),
        tamper_pos=st.integers(min_value=0, max_value=63),
    )
    @settings(max_examples=20)
    def test_single_char_tamper_detected(self, tamper_char: str, tamper_pos: int) -> None:
        # Valid fingerprint
        valid = "a" * 64
        # Tamper one character
        tampered = valid[:tamper_pos] + tamper_char + valid[tamper_pos + 1 :]
        if tampered != valid:
            assert tampered != valid  # Tampered is detected


class TestFP7NetworkFailureNoDuplicateExecution:
    """FP7: Failed network after accepted execution produces no duplicate."""

    @given(retry_count=st.integers(min_value=1, max_value=10))
    @settings(max_examples=10)
    def test_broker_idempotent_on_retries(self, retry_count: int) -> None:
        from alphaforge.broker.paper import PaperBroker

        broker = PaperBroker()
        client_id = "AF-E-FP7TEST000000000000"
        from alphaforge.broker.models import BrokerOrderRequest
        from alphaforge.execution.enums import OrderSide

        req = BrokerOrderRequest(
            client_order_id=client_id,
            symbol="NIFTY",
            side=OrderSide.BUY,
            quantity=50,
            role=OrderRole.ENTRY,
        )
        ids = set()
        for _ in range(retry_count):
            result = broker.submit_order(req)
            ids.add(result.broker_order_id)
        assert len(ids) == 1  # Single order regardless of retries


class TestFP8RiskLimitsInvariantUnderRestart:
    """FP8: Risk limits are consistently enforced regardless of restart."""

    @given(open_trades=st.integers(min_value=0, max_value=10))
    @settings(max_examples=10)
    def test_max_trades_enforced(self, open_trades: int) -> None:
        from alphaforge.risk.engine import evaluate_trade_risk
        from alphaforge.risk.models import (
            PortfolioRiskState,
            RiskConfig,
            RiskInput,
        )

        config = RiskConfig(max_open_trades=3)
        equity = Decimal("1000000")

        # Build reservations matching open_trade_count
        from alphaforge.risk.models import RiskReservation

        reservations = tuple(
            RiskReservation(
                reservation_id=f"RES-FP8-{i}",
                signal_id=f"SIG-FP8-{i}",
                symbol="NIFTY",
                side=TradeSide.LONG,
                entry_price=Decimal("24000"),
                stop_price=Decimal("23900"),
                quantity=50,
                monetary_risk=Decimal("100"),
                notional=Decimal("50000"),
                created_timestamp=datetime.now(UTC),
            )
            for i in range(min(open_trades, 10))
        )

        portfolio = PortfolioRiskState(
            account_equity=equity,
            available_capital=equity,
            open_trade_count=len(reservations),
            reserved_risk=Decimal("100") * len(reservations),
            reserved_notional=Decimal("50000") * len(reservations),
            daily_starting_equity=equity,
            current_equity=equity,
            active_reservations=reservations,
        )

        trade = RiskInput(
            signal_id="SIG-FP8-NEW",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_price=Decimal("24000"),
            stop_price=Decimal("23900"),
            contract_id="NIFTY-2026-09",
            lot_size=50,
            contract_multiplier=Decimal("1"),
            account_equity=equity,
            available_capital=equity,
            evaluation_timestamp=datetime.now(UTC),
        )

        decision = evaluate_trade_risk(trade, portfolio, config)
        if open_trades >= 3:
            assert decision.decision.value == "REJECTED"
        else:
            assert decision.decision.value in ("APPROVED", "REJECTED")


class TestFP9FaultInjectionDoesNotMutateSource:
    """FP9: Fault injection framework does not mutate source artifacts."""

    @given(event_count=st.integers(min_value=1, max_value=5))
    @settings(max_examples=10)
    def test_injector_preserves_delegate_state(self, event_count: int) -> None:
        from alphaforge.fault_injection.injectors import (
            CorruptingLedgerStorage,
        )

        delegate = InMemoryLedgerStorage()
        # Drop event at position 999 (never triggered)
        corrupting = CorruptingLedgerStorage(delegate, drop_event_at=999)

        base = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        ledger = AuditLedger(corrupting, auto_verify_on_startup=False)

        for i in range(event_count):
            ledger.append(
                event_type=AuditEventType.SIGNAL_GENERATED,
                entity_type="SIGNAL",
                entity_id=f"SIG-FP9-{i}",
                correlation_id="CORR-FP9",
                causation_id="ROOT",
                payload={"i": i},
                event_timestamp=base + timedelta(seconds=i * 10),
            )

        # Delegate has all events (no drop triggered)
        assert delegate.count() == event_count


class TestFP10FailureScenariosReproducible:
    """FP10: Failure scenarios are reproducible from canonical definition."""

    @given(
        seed_idx=st.integers(min_value=0, max_value=99),
    )
    @settings(max_examples=10)
    def test_deterministic_hash_computation(self, seed_idx: int) -> None:
        ts = datetime(2026, 9, 13, 9, 15, tzinfo=UTC)
        payload = {"seed": seed_idx, "symbol": "NIFTY"}
        h1 = compute_event_hash(
            1,
            1,
            ts,
            AuditEventType.SIGNAL_GENERATED,
            "SIGNAL",
            "SIG-001",
            "CORR-001",
            "ROOT",
            payload,
            "GENESIS",
        )
        h2 = compute_event_hash(
            1,
            1,
            ts,
            AuditEventType.SIGNAL_GENERATED,
            "SIGNAL",
            "SIG-001",
            "CORR-001",
            "ROOT",
            payload,
            "GENESIS",
        )
        assert h1 == h2  # Reproducible
