"""
AlphaForge In-Memory Canonical Candle Store.
Provides deterministic, auditable in-memory storage and retrieval of normalized MarketCandles.
Enforces closed-candle quarantine and bridges directly to Phase 1 Strategy Engine.
"""

from collections.abc import Sequence
from datetime import datetime

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.core.models import Candle
from alphaforge.data.models import MarketCandle
from alphaforge.data.timeframe import expected_next_timestamp


class CandleStore:
    """
    In-memory deterministic store for validated and normalized MarketCandles.
    Maintains strictly chronological closed candle series and separate forming candle state.
    """

    def __init__(self) -> None:
        # Key: (symbol, timeframe) -> sorted list of closed MarketCandle
        self._closed_store: dict[tuple[str, str], list[MarketCandle]] = {}
        # Key: (symbol, timeframe) -> latest forming MarketCandle (is_closed=False)
        self._forming_store: dict[tuple[str, str], MarketCandle] = {}

    def clear(self) -> None:
        """Clear all stored market data."""
        self._closed_store.clear()
        self._forming_store.clear()

    def add_candle(self, candle: MarketCandle) -> None:
        """
        Store a normalized MarketCandle.
        Closed candles are inserted chronologically into closed store.
        Forming candles (is_closed=False) update forming store.
        """
        key = (candle.symbol, candle.timeframe)

        if not candle.is_closed:
            self._forming_store[key] = candle
            return

        if key not in self._closed_store:
            self._closed_store[key] = [candle]
            return

        series = self._closed_store[key]

        # Check for existing candle at same timestamp
        for _i, existing in enumerate(series):
            if existing.exchange_timestamp == candle.exchange_timestamp:
                # Check for identical duplicate vs conflict
                if (
                    existing.open == candle.open
                    and existing.high == candle.high
                    and existing.low == candle.low
                    and existing.close == candle.close
                    and existing.volume == candle.volume
                    and existing.open_interest == candle.open_interest
                ):
                    # Idempotent: identical duplicate already present
                    return
                raise DataIntegrityError(
                    f"Conflicting candle insertion at {candle.exchange_timestamp} "
                    f"for key {candle.symbol}:{candle.timeframe}"
                )

        # Insert and maintain chronological order
        series.append(candle)
        series.sort(key=lambda c: c.exchange_timestamp)

    def add_candles(self, candles: Sequence[MarketCandle]) -> None:
        """Add multiple candles in a batch."""
        for c in candles:
            self.add_candle(c)

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[MarketCandle]:
        """
        Retrieve closed candles matching query criteria in chronological order.
        """
        key = (symbol.upper(), timeframe)
        series = self._closed_store.get(key, [])
        if not series:
            return []

        filtered = series
        if start_time is not None:
            filtered = [c for c in filtered if c.exchange_timestamp >= start_time]
        if end_time is not None:
            filtered = [c for c in filtered if c.exchange_timestamp <= end_time]

        return list(filtered)

    def get_latest_candle(self, symbol: str, timeframe: str) -> MarketCandle | None:
        """Return the latest closed candle for the specified symbol and timeframe."""
        key = (symbol.upper(), timeframe)
        series = self._closed_store.get(key, [])
        if not series:
            return None
        return series[-1]

    def get_forming_candle(self, symbol: str, timeframe: str) -> MarketCandle | None:
        """Return the currently forming (unclosed) candle if available."""
        key = (symbol.upper(), timeframe)
        return self._forming_store.get(key)

    def count(self, symbol: str, timeframe: str) -> int:
        """Return count of closed candles for symbol and timeframe."""
        key = (symbol.upper(), timeframe)
        return len(self._closed_store.get(key, []))

    def get_strategy_execution_input(
        self,
        symbol: str,
        timeframe: str,
        count: int,
        forming_candle: MarketCandle | None = None,
    ) -> list[Candle]:
        """
        Build the canonical execution candle sequence for Phase 1 DeterministicStrategyEngine.

        Contract:
          - index [0]: forming candle (is_closed=False, quarantined by strategy)
          - index [1]: latest fully closed candle (trigger candle)
          - index [2..N]: prior fully closed candles in reverse chronological order
        """
        key = (symbol.upper(), timeframe)
        closed_series = self._closed_store.get(key, [])
        if not closed_series:
            return []

        # Latest 'count' closed candles in reverse chronological order
        recent_closed = closed_series[-count:]
        reversed_closed = [c.to_strategy_candle() for c in reversed(recent_closed)]

        # Determine forming candle for index [0]
        forming = forming_candle or self._forming_store.get(key)
        if forming is not None:
            forming_strategy_candle = Candle(
                timestamp=forming.exchange_timestamp,
                open=forming.open,
                high=forming.high,
                low=forming.low,
                close=forming.close,
                volume=forming.volume,
                open_interest=forming.open_interest,
                is_closed=False,
            )
        else:
            # Construct a safe placeholder forming candle with is_closed=False
            last_closed = recent_closed[-1]
            next_ts = expected_next_timestamp(last_closed.exchange_timestamp, timeframe)
            forming_strategy_candle = Candle(
                timestamp=next_ts,
                open=last_closed.close,
                high=last_closed.close,
                low=last_closed.close,
                close=last_closed.close,
                volume=0,
                is_closed=False,
            )

        return [forming_strategy_candle, *reversed_closed]

    def get_strategy_confirmation_input(
        self,
        symbol: str,
        timeframe: str,
        max_timestamp: datetime,
        count: int | None = None,
    ) -> list[Candle]:
        """
        Build the confirmation candle sequence for Phase 1 DeterministicStrategyEngine.
        Returns closed candles on or before max_timestamp in chronological order.
        """
        key = (symbol.upper(), timeframe)
        closed_series = self._closed_store.get(key, [])
        if not closed_series:
            return []

        eligible = [c for c in closed_series if c.exchange_timestamp <= max_timestamp]
        if count is not None and len(eligible) > count:
            eligible = eligible[-count:]

        return [c.to_strategy_candle() for c in eligible]
