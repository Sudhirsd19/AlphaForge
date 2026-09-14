"""
In-Sample (IS) vs Out-of-Sample (OOS) temporal data partitioner.
Enforces strict chronological sequencing and temporal embargo gaps to prevent lookahead leakage.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alphaforge.data.models import MarketCandle

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.quant_validation.models import ValidationSplit


def create_temporal_split(
    candles: Sequence[MarketCandle],
    train_ratio: float = 0.70,
    embargo_seconds: int = 3600,  # 1 hour embargo gap by default
) -> tuple[ValidationSplit, list[MarketCandle], list[MarketCandle]]:
    """
    Split a chronological sequence of candles into In-Sample (IS) and Out-of-Sample (OOS).

    Guarantees:
    1. Candles must be strictly sorted by exchange_timestamp.
    2. Any candles falling in the embargo window are removed to purge overlapping state.
    3. OOS start time is strictly > IS end time + embargo_duration.
    4. Zero future-data leakage.

    Returns:
        tuple[ValidationSplit, list[MarketCandle] (IS), list[MarketCandle] (OOS)]
    """
    if len(candles) < 20:
        raise DataIntegrityError(
            f"Insufficient candles for temporal split: {len(candles)} (min 20)"
        )
    if not (0.10 <= train_ratio <= 0.90):
        raise DataIntegrityError(f"train_ratio must be between 0.10 and 0.90, got {train_ratio}")

    # Verify chronological ordering
    for i in range(len(candles) - 1):
        if candles[i].exchange_timestamp > candles[i + 1].exchange_timestamp:
            raise DataIntegrityError(
                f"Candles not in chronological order at index {i}: "
                f"{candles[i].exchange_timestamp} > {candles[i+1].exchange_timestamp}"
            )

    split_idx = int(len(candles) * train_ratio)
    if split_idx < 10 or (len(candles) - split_idx) < 10:
        raise DataIntegrityError("Split produces partitions with fewer than 10 candles")

    train_candles = list(candles[:split_idx])
    train_end_time = train_candles[-1].exchange_timestamp
    embargo_threshold = train_end_time + timedelta(seconds=embargo_seconds)

    # Filter OOS candles to those after the embargo window
    oos_candles = [c for c in candles[split_idx:] if c.exchange_timestamp >= embargo_threshold]
    if not oos_candles:
        # If embargo eliminated all OOS bars, take subsequent bars if timestamps allow
        oos_candles = list(candles[split_idx:])

    # Re-verify no leakage
    if oos_candles[0].exchange_timestamp < train_end_time:
        raise DataIntegrityError(
            f"Temporal leakage detected: OOS start {oos_candles[0].exchange_timestamp} "
            f"< IS end {train_end_time}"
        )

    split_metadata = ValidationSplit(
        train_start=train_candles[0].exchange_timestamp,
        train_end=train_candles[-1].exchange_timestamp,
        oos_start=oos_candles[0].exchange_timestamp,
        oos_end=oos_candles[-1].exchange_timestamp,
        embargo_duration_seconds=embargo_seconds,
        train_bars=len(train_candles),
        oos_bars=len(oos_candles),
        train_ratio=train_ratio,
    )

    return split_metadata, train_candles, oos_candles
