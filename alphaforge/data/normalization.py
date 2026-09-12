"""
AlphaForge Market Data Normalization and Arbiter.
Implements deterministic sorting, deduplication, conflict quarantine, gap detection,
and data quality state assignment. Zero data imputation is guaranteed.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import (
    DataQualityStatus,
    most_severe_status,
)
from alphaforge.data.models import MarketCandle, RawMarketRecord
from alphaforge.data.timeframe import has_candle_gap
from alphaforge.data.validation import parse_raw_record


class QuarantinedRecord(BaseModel):
    """Immutable audit record of quarantined/rejected market data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_data: dict[str, Any] = Field(
        description="Raw payload that failed validation or caused conflict"
    )
    reason: str = Field(description="Human-readable root cause explanation")
    status: DataQualityStatus = Field(description="INVALID or CONFLICT")
    quarantine_timestamp: datetime = Field(description="UTC timestamp when record was quarantined")


class NormalizationResult(BaseModel):
    """
    Deterministic result of normalizer execution on a batch of market records.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_candles: list[MarketCandle] = Field(
        description="Clean, chronological, deduplicated, gap-checked candle sequence"
    )
    quarantined_records: list[QuarantinedRecord] = Field(
        default_factory=list, description="Quarantined invalid or conflicting records"
    )
    duplicates_count: int = Field(
        default=0, description="Count of identical duplicate records safely collapsed"
    )
    detected_gaps: list[tuple[datetime, datetime]] = Field(
        default_factory=list, description="Timestamp interval bounds where gaps occurred"
    )
    quality_status: DataQualityStatus = Field(
        description="Overall batch quality classification according to precedence hierarchy"
    )
    was_out_of_order: bool = Field(
        default=False, description="True if raw records were received out of chronological order"
    )


class MarketDataNormalizer:
    """
    Normalizes raw market data into canonical, deterministic MarketCandle sequences.
    Enforces strict chronological sorting, duplicate deduplication, conflict quarantine,
    and gap detection with zero synthetic data imputation.
    """

    def __init__(
        self,
        default_timeframe: str = "3m",
        max_stale_seconds: int = 195,
        min_required_candles: int = 1,
    ) -> None:
        self.default_timeframe = default_timeframe
        self.max_stale_seconds = max_stale_seconds
        self.min_required_candles = min_required_candles

    def normalize_batch(
        self,
        records: Sequence[RawMarketRecord | MarketCandle | dict[str, Any]],
        evaluation_timestamp: datetime | None = None,
        timeframe: str | None = None,
    ) -> NormalizationResult:
        """
        Execute full normalization pipeline on an input batch.
        Guarantees deterministic, reproducible output.
        """
        now_utc = evaluation_timestamp or datetime.now(UTC)
        target_tf = timeframe or self.default_timeframe

        if not records:
            return NormalizationResult(
                valid_candles=[],
                quarantined_records=[],
                duplicates_count=0,
                detected_gaps=[],
                quality_status=DataQualityStatus.EMPTY,
                was_out_of_order=False,
            )

        parsed_candidates: list[MarketCandle] = []
        quarantined: list[QuarantinedRecord] = []
        observed_statuses: list[DataQualityStatus] = []

        # 1. Validation & Parsing
        for item in records:
            if isinstance(item, MarketCandle):
                parsed_candidates.append(item)
            else:
                try:
                    candle = parse_raw_record(item, evaluation_timestamp=evaluation_timestamp)
                    parsed_candidates.append(candle)
                except DataIntegrityError as err:
                    raw_dict = (
                        item.model_dump()
                        if isinstance(item, RawMarketRecord)
                        else (item if isinstance(item, dict) else {"raw": str(item)})
                    )
                    quarantined.append(
                        QuarantinedRecord(
                            raw_data=raw_dict,
                            reason=str(err),
                            status=DataQualityStatus.INVALID,
                            quarantine_timestamp=now_utc,
                        )
                    )
                    observed_statuses.append(DataQualityStatus.INVALID)

        if not parsed_candidates and quarantined:
            return NormalizationResult(
                valid_candles=[],
                quarantined_records=quarantined,
                duplicates_count=0,
                detected_gaps=[],
                quality_status=most_severe_status(observed_statuses),
                was_out_of_order=False,
            )

        # 2. Check chronological ordering
        was_out_of_order = False
        for i in range(len(parsed_candidates) - 1):
            cur_ts = parsed_candidates[i].exchange_timestamp
            next_ts = parsed_candidates[i + 1].exchange_timestamp
            if cur_ts > next_ts:
                was_out_of_order = True
                observed_statuses.append(DataQualityStatus.OUT_OF_ORDER)
                break

        # 3. Deterministic Sort
        # Sort key: (exchange_timestamp, symbol, open, high, low, close, volume)
        sorted_candidates = sorted(
            parsed_candidates,
            key=lambda c: (
                c.exchange_timestamp,
                c.symbol,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ),
        )

        # 4. Duplicate & Conflict Resolution
        # Group by composite key: (symbol, timeframe, exchange_timestamp)
        grouped: dict[tuple[str, str, datetime], list[MarketCandle]] = defaultdict(list)
        for c in sorted_candidates:
            grouped[(c.symbol, c.timeframe, c.exchange_timestamp)].append(c)

        deduped_candles: list[MarketCandle] = []
        duplicates_count = 0

        for key, group in grouped.items():
            if len(group) == 1:
                deduped_candles.append(group[0])
            else:
                # Multiple records for same key
                # Check if all records in group have identical OHLCV and open_interest
                first = group[0]
                is_identical = all(
                    c.open == first.open
                    and c.high == first.high
                    and c.low == first.low
                    and c.close == first.close
                    and c.volume == first.volume
                    and c.open_interest == first.open_interest
                    for c in group[1:]
                )

                if is_identical:
                    # Idempotent deduplication: retain exactly one
                    deduped_candles.append(first)
                    duplicates_count += len(group) - 1
                    observed_statuses.append(DataQualityStatus.DUPLICATE)
                else:
                    # Divergent OHLCV at same timestamp: CONFLICT!
                    # Quarantine all divergent records without silent overwrite
                    for c in group:
                        quarantined.append(
                            QuarantinedRecord(
                                raw_data=c.model_dump(mode="json"),
                                reason=(
                                    f"Conflict: differing OHLCV for composite key "
                                    f"{key[0]}:{key[1]} at {key[2].isoformat()}"
                                ),
                                status=DataQualityStatus.CONFLICT,
                                quarantine_timestamp=now_utc,
                            )
                        )
                    observed_statuses.append(DataQualityStatus.CONFLICT)

        # Re-sort clean deduped candles
        deduped_candles.sort(key=lambda c: c.exchange_timestamp)

        # 5. Gap Detection
        detected_gaps: list[tuple[datetime, datetime]] = []
        if len(deduped_candles) > 1:
            for i in range(len(deduped_candles) - 1):
                prev_c = deduped_candles[i]
                curr_c = deduped_candles[i + 1]
                if has_candle_gap(prev_c.exchange_timestamp, curr_c.exchange_timestamp, target_tf):
                    detected_gaps.append((prev_c.exchange_timestamp, curr_c.exchange_timestamp))
                    observed_statuses.append(DataQualityStatus.GAP)

        # 6. Freshness / Stale Check
        if evaluation_timestamp is not None and deduped_candles:
            latest_ts = deduped_candles[-1].exchange_timestamp
            age_seconds = (evaluation_timestamp - latest_ts).total_seconds()
            if age_seconds > self.max_stale_seconds:
                observed_statuses.append(DataQualityStatus.STALE)

        # 7. Incomplete Check
        if len(deduped_candles) < self.min_required_candles:
            observed_statuses.append(DataQualityStatus.INCOMPLETE)

        # If no defects observed, status is VALID
        if not observed_statuses:
            observed_statuses.append(DataQualityStatus.VALID)

        final_status = most_severe_status(observed_statuses)

        # Update quality_status on valid candles to reflect final status
        final_valid_candles = [
            c.model_copy(update={"quality_status": final_status}) for c in deduped_candles
        ]

        return NormalizationResult(
            valid_candles=final_valid_candles,
            quarantined_records=quarantined,
            duplicates_count=duplicates_count,
            detected_gaps=detected_gaps,
            quality_status=final_status,
            was_out_of_order=was_out_of_order,
        )
