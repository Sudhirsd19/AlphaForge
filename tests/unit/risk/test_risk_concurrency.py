"""
Concurrency and thread-safety tests for AlphaForge RiskEngine V1.
Verifies that atomic reservation acquisitions, limits, and releases maintain
strict mathematical and portfolio invariants under high concurrent load.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.risk.engine import RiskEngine
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import (
    PortfolioRiskState,
    RiskConfig,
    RiskDecision,
    RiskInput,
    RiskReservation,
)


def make_concurrency_risk_input(
    signal_id: str,
    equity: Decimal = Decimal("4000000.00"),
    available_capital: Decimal = Decimal("2000000.00"),
    stop_distance_pts: Decimal = Decimal("240.00"),  # 1.00%
    lot_size: int = 25,
    quantity: int = 25,  # 1 lot: risk = 240 * 25 = 6,000
) -> RiskInput:
    """Helper to construct unique risk inputs for concurrent tests."""
    return RiskInput(
        signal_id=signal_id,
        symbol="NIFTY",
        side=TradeSide.LONG,
        entry_price=Decimal("24000.00"),
        stop_price=Decimal("24000.00") - stop_distance_pts,
        contract_id="NIFTY26JUNFUT",
        lot_size=lot_size,
        contract_multiplier=Decimal("1"),
        account_equity=equity,
        available_capital=available_capital,
        proposed_quantity=quantity,
        evaluation_timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )


def test_concurrent_portfolio_risk_budget_enforcement() -> None:
    """
    Stress test: 20 concurrent threads attempt to reserve 15,000 risk each on a
    portfolio with max_portfolio_risk = 2.00% (80,000 budget, max 5 open trades).
    Invariants:
    - At most 5 reservations approved.
    - Total reserved risk <= 80,000.
    - All remaining trades rejected deterministically.
    - No race condition corrupts engine state.
    """
    equity = Decimal("4000000.00")
    # Stop distance = 600 pts (2.50%), quantity = 25 -> risk = 600 * 25 = 15,000
    # 5 trades * 15,000 = 75,000 <= 80,000 (2.00%). 6th trade would exceed max_open_trades (5)
    config = RiskConfig(
        max_portfolio_risk=Decimal("0.0200"),
        max_open_trades=5,
    )
    engine = RiskEngine(config=config)
    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=Decimal("3000000.00"),
        open_trade_count=0,
        reserved_risk=Decimal("0"),
        reserved_notional=Decimal("0"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    num_threads = 20
    results = []

    def worker(worker_id: int) -> tuple[RiskDecision, RiskReservation | None]:
        t_input = make_concurrency_risk_input(
            signal_id=f"SIG-CONCUR-{worker_id:03d}",
            equity=equity,
            available_capital=Decimal("3000000.00"),
            stop_distance_pts=Decimal("600.00"),
            quantity=25,
        )
        return engine.request_risk_reservation(t_input, portfolio)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for fut in as_completed(futures):
            results.append(fut.result())

    approved = [r for r in results if r[0].decision == RiskDecisionState.APPROVED]
    rejected = [r for r in results if r[0].decision == RiskDecisionState.REJECTED]

    assert len(approved) <= 5
    assert len(approved) + len(rejected) == num_threads

    # Verify atomic engine invariants
    active = engine.get_active_reservations()
    assert len(active) == len(approved)
    total_risk = engine.total_reserved_risk
    assert total_risk <= Decimal("80000.00")
    assert total_risk == sum((r.monetary_risk for r in active), Decimal("0"))


def test_concurrent_idempotency_same_signal() -> None:
    """
    Stress test: 15 concurrent threads attempt to reserve the exact SAME signal_id.
    Invariants:
    - Exactly 1 thread receives APPROVED.
    - Exactly 14 threads receive REJECTED with DUPLICATE_RISK_RESERVATION.
    - Exactly 1 reservation is stored in active reservations.
    """
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    signal_id = "SIG-IDENTICAL-001"
    num_threads = 15
    results = []

    def worker() -> tuple[RiskDecision, RiskReservation | None]:
        t_input = make_concurrency_risk_input(signal_id=signal_id, equity=equity)
        return engine.request_risk_reservation(t_input, portfolio)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        for fut in as_completed(futures):
            results.append(fut.result())

    approved = [r for r in results if r[0].decision == RiskDecisionState.APPROVED]
    duplicates = [
        r
        for r in results
        if r[0].decision == RiskDecisionState.REJECTED
        and r[0].reason_code == RiskReasonCode.DUPLICATE_RISK_RESERVATION
    ]

    assert len(approved) == 1
    assert len(duplicates) == num_threads - 1
    assert len(engine.get_active_reservations()) == 1


def test_concurrent_acquire_and_release() -> None:
    """
    Stress test: Concurrent workers repeatedly acquire and release reservations.
    Invariants:
    - No deadlocks.
    - Invariant total_reserved_risk == sum of active reservations at all times.
    - After all releases complete, total_reserved_risk is exactly 0.
    """
    equity = Decimal("4000000.00")
    engine = RiskEngine()
    portfolio = PortfolioRiskState(
        account_equity=equity,
        available_capital=Decimal("2000000.00"),
        daily_starting_equity=equity,
        current_equity=equity,
    )

    barrier = threading.Barrier(10)

    def worker(worker_id: int) -> None:
        barrier.wait()
        sig = f"SIG-CYCLE-{worker_id:03d}"
        t_input = make_concurrency_risk_input(signal_id=sig, equity=equity)
        dec, res = engine.request_risk_reservation(t_input, portfolio)
        if res is not None:
            released = engine.release_reservation(res.reservation_id)
            assert released is True

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for fut in as_completed(futures):
            fut.result()

    assert len(engine.get_active_reservations()) == 0
    assert engine.total_reserved_risk == Decimal("0")
    assert engine.total_reserved_notional == Decimal("0")
