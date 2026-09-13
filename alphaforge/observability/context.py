"""
AlphaForge Causal Trace Context.

Provides async-safe and thread-safe correlation and causation propagation
using contextvars, supporting nested spans, automatic token reset, and cross-task isolation.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_current_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_current_causation_id: ContextVar[str | None] = ContextVar("causation_id", default=None)
_current_span_name: ContextVar[str | None] = ContextVar("span_name", default=None)
_correlation_lock = threading.Lock()
_correlation_sequences: dict[str, int] = {}


class TraceContext:
    """
    Static accessor and manager for causal trace identifiers.

    Invariants:
    - correlation_id represents the unbroken end-to-end trading lifecycle (e.g. signal to fill).
    - causation_id represents the immediate preceding causal event identifier.
    - Causal context propagation uses contextvars for async task and thread inheritance.
    - Intra-correlation sequence counters use a synchronized registry across child tasks.
    """

    @classmethod
    def get_correlation_id(cls) -> str | None:
        """Return the active correlation_id in the current context."""
        return _current_correlation_id.get()

    @classmethod
    def get_causation_id(cls) -> str | None:
        """Return the active causation_id in the current context."""
        return _current_causation_id.get()

    @classmethod
    def get_span_name(cls) -> str | None:
        """Return the active span name in the current context."""
        return _current_span_name.get()

    @classmethod
    def next_sequence(cls, correlation_id: str | None = None) -> int:
        """
        Return the next monotonic sequence ordinal for the given correlation chain.
        Thread-safe and async-safe across concurrent tasks and threads sharing a correlation.
        Guarantees deterministic, collision-free event ordering.
        """
        cid = correlation_id or cls.get_correlation_id() or "__root__"
        with _correlation_lock:
            seq = _correlation_sequences.get(cid, 0) + 1
            _correlation_sequences[cid] = seq
            return seq

    @classmethod
    def get_current_sequence(cls, correlation_id: str | None = None) -> int:
        """Return the current sequence ordinal for the correlation chain without incrementing."""
        cid = correlation_id or cls.get_correlation_id() or "__root__"
        with _correlation_lock:
            return _correlation_sequences.get(cid, 0)

    @classmethod
    def reset_sequence(cls, correlation_id: str | None = None) -> None:
        """Reset sequence ordinal for a specific correlation ID or all correlations if None."""
        with _correlation_lock:
            if correlation_id is None:
                _correlation_sequences.clear()
            else:
                _correlation_sequences.pop(correlation_id, None)

    @classmethod
    def set_context(
        cls,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[Token[str | None], Token[str | None]]:
        """
        Explicitly set active correlation and causation IDs.
        Returns tokens that can be used for manual reset if not using context manager.
        """
        tok_corr = _current_correlation_id.set(correlation_id)
        tok_caus = _current_causation_id.set(causation_id)
        return tok_corr, tok_caus

    @classmethod
    def set_causation_id(cls, causation_id: str | None) -> Token[str | None]:
        """Update only the active causation_id (e.g. after emitting an event)."""
        return _current_causation_id.set(causation_id)

    @classmethod
    def clear(cls) -> None:
        """Reset all trace context variables to None and clear all correlation sequences."""
        _current_correlation_id.set(None)
        _current_causation_id.set(None)
        _current_span_name.set(None)
        with _correlation_lock:
            _correlation_sequences.clear()


@contextmanager
def trace_span(
    name: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> Iterator[dict[str, str | None]]:
    """
    Context manager establishing a nested causal trace span.

    Behavior:
    - If correlation_id is not specified, inherits parent correlation_id.
    - If causation_id is not specified, inherits parent causation_id.
    - Guaranteed restoration of previous tokens on scope exit (even during exceptions).
    """
    parent_corr = _current_correlation_id.get()
    parent_caus = _current_causation_id.get()

    effective_corr = correlation_id if correlation_id is not None else parent_corr
    effective_caus = causation_id if causation_id is not None else parent_caus

    token_corr = _current_correlation_id.set(effective_corr)
    token_caus = _current_causation_id.set(effective_caus)
    token_name = _current_span_name.set(name)

    span_info = {
        "span_name": name,
        "correlation_id": effective_corr,
        "causation_id": effective_caus,
    }

    try:
        yield span_info
    finally:
        _current_correlation_id.reset(token_corr)
        _current_causation_id.reset(token_caus)
        _current_span_name.reset(token_name)
