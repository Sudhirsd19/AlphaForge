"""
AlphaForge Controlled Forward Validation Runner.

Executes reproducible forward paper/shadow trading runs over market data streams.
Validates determinism, continuous reconciliation, and generates forensic ForwardRunReports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alphaforge.paper_shadow.engine import PaperShadowEngine
from alphaforge.paper_shadow.models import (
    ForwardRunReport,
    MarketEvent,
    PaperShadowConfig,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alphaforge.data.models import MarketCandle


class ForwardValidationRunner:
    """
    Controlled Forward Validation testbench for Paper and Shadow Trading.
    Enforces deterministic reproducibility and explicit boundary isolation.
    """

    def __init__(
        self,
        config: PaperShadowConfig | None = None,
        git_commit: str = "81fba27684d848beecbd6d2d3e8f484fd090b0fe",
    ) -> None:
        self._config = config or PaperShadowConfig()
        self._git_commit = git_commit
        self._engine = PaperShadowEngine(
            config=self._config,
            git_commit=self._git_commit,
        )

    @property
    def engine(self) -> PaperShadowEngine:
        return self._engine

    def run_stream(self, candles: Sequence[MarketCandle]) -> ForwardRunReport:
        """
        Execute forward validation over a sequential candle stream.
        Processes each candle, runs continuous reconciliation, and shuts down cleanly.
        """
        for i, candle in enumerate(candles):
            # Form causal receipt timestamp
            receipt_ts = candle.exchange_timestamp
            event_id = f"EVT-{candle.symbol}-{i:06d}"
            self._engine.process_candle(
                candle=candle,
                receipt_timestamp=receipt_ts,
                market_event_id=event_id,
            )

        # Clean shutdown and report generation
        return self._engine.shutdown()

    def run_events(self, events: Sequence[MarketEvent]) -> ForwardRunReport:
        """
        Execute forward validation over a sequential MarketEvent stream.
        """
        for event in events:
            self._engine.process_event(event)

        return self._engine.shutdown()
