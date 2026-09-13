"""
AlphaForge Paper / Shadow Trading Enumerations.

Defines authoritative enumerations for Paper and Shadow execution modes, fill simulation policies,
market data anomalies, shadow comparison outcomes, and forward validation run states.
"""

from __future__ import annotations

from enum import StrEnum


class PaperShadowMode(StrEnum):
    """
    Authoritative operational mode for Phase 16 validation.

    - PAPER: Executes forward trading on simulated broker with continuous reconciliation.
    - SHADOW: Observes market events passively without submitting broker orders.
    """

    PAPER = "PAPER"
    SHADOW = "SHADOW"

    @property
    def is_paper(self) -> bool:
        """Return True if mode is PAPER."""
        return self == PaperShadowMode.PAPER

    @property
    def is_shadow(self) -> bool:
        """Return True if mode is SHADOW."""
        return self == PaperShadowMode.SHADOW


class OHLCResolutionPolicy(StrEnum):
    """
    Deterministic resolution policy for same-bar Stop-Loss and Take-Profit touches.

    - SL_FIRST_CONSERVATIVE: When both SL and TP price levels are within the candle high-low range,
      strictly triggers Stop-Loss first to prevent optimistic bias. (Authoritative default).
    - TP_FIRST_AGGRESSIVE: Triggers Take-Profit first (only for explicit sensitivity testing).
    """

    SL_FIRST_CONSERVATIVE = "SL_FIRST_CONSERVATIVE"
    TP_FIRST_AGGRESSIVE = "TP_FIRST_AGGRESSIVE"


class FillType(StrEnum):
    """Execution fill classification."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    FORCED_CLOSE = "FORCED_CLOSE"


class MarketDataAnomalyType(StrEnum):
    """
    Classification of market data anomalies intercepted during forward validation.
    """

    TIMESTAMP_OUT_OF_ORDER = "TIMESTAMP_OUT_OF_ORDER"
    STALE_DATA = "STALE_DATA"
    DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
    GAP_DETECTED = "GAP_DETECTED"
    FORMING_BAR_LEAK = "FORMING_BAR_LEAK"
    INVALID_OHLC_BOUNDARY = "INVALID_OHLC_BOUNDARY"
    SESSION_OUT_OF_BOUNDS = "SESSION_OUT_OF_BOUNDS"
    LOT_SIZE_MISMATCH = "LOT_SIZE_MISMATCH"


class OrderRoutingMode(StrEnum):
    """Routing destination for order intents."""

    PAPER_BROKER = "PAPER_BROKER"
    SHADOW_VIRTUAL = "SHADOW_VIRTUAL"


class ShadowComparisonOutcome(StrEnum):
    """Diagnostic comparison outcome in SHADOW mode."""

    MATCH_SIMULATED = "MATCH_SIMULATED"
    MISMATCH_EXECUTION = "MISMATCH_EXECUTION"
    MISMATCH_PRICE = "MISMATCH_PRICE"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    NO_SIGNAL = "NO_SIGNAL"


class ForwardRunState(StrEnum):
    """Lifecycle state of the Forward Validation Engine."""

    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"
