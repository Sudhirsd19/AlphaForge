"""
Unit tests for AlphaForge Continuous Reconciler in Paper / Shadow Trading.
"""

from __future__ import annotations

from decimal import Decimal

from alphaforge.broker.models import BrokerPosition
from alphaforge.broker.paper import PaperBroker
from alphaforge.ledger.ledger import AuditLedger
from alphaforge.ledger.storage import InMemoryLedgerStorage
from alphaforge.paper_shadow.enums import PaperShadowMode
from alphaforge.paper_shadow.models import PaperShadowConfig
from alphaforge.paper_shadow.order_router import PaperShadowOrderRouter
from alphaforge.paper_shadow.pnl_tracker import PaperPnLTracker
from alphaforge.paper_shadow.reconciler_adapter import PaperShadowReconciler
from alphaforge.reconciliation.models import ReconciliationStatus
from alphaforge.risk.enums import TradeSide


def test_reconciler_clean_state_aligned() -> None:
    broker = PaperBroker()
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=cfg, broker=broker)
    tracker = PaperPnLTracker()
    ledger = AuditLedger(storage=InMemoryLedgerStorage())

    reconciler = PaperShadowReconciler(
        order_router=router,
        pnl_tracker=tracker,
        broker=broker,
        ledger=ledger,
    )

    res = reconciler.reconcile()
    assert res.status == ReconciliationStatus.MATCHED
    assert res.mismatch_count == 0
    assert res.new_entries_allowed is True


def test_reconciler_detects_phantom_broker_position() -> None:
    broker = PaperBroker()
    cfg = PaperShadowConfig(mode=PaperShadowMode.PAPER)
    router = PaperShadowOrderRouter(config=cfg, broker=broker)
    tracker = PaperPnLTracker()

    reconciler = PaperShadowReconciler(
        order_router=router,
        pnl_tracker=tracker,
        broker=broker,
    )

    # Inject external untracked position into broker
    broker.inject_external_position(
        BrokerPosition(
            position_id="POS-001",
            symbol="NIFTY",
            side=TradeSide.LONG,
            quantity=50,
            average_price=Decimal("20000"),
            status="OPEN",
        )
    )

    res = reconciler.reconcile()
    assert res.status == ReconciliationStatus.MISMATCH
    assert res.mismatch_count > 0
    assert res.new_entries_allowed is False
    assert any("Phantom Position" in str(d) for d in res.position_details)
