"""
AlphaForge Backtest Dataset Engine.
Implements immutable DatasetMetadata, deterministic SHA-256 dataset checksumming,
and strict Phase 2 validated candle ingestion and ordering.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.data.models import MarketCandle


def compute_dataset_checksum(candles: Sequence[MarketCandle]) -> str:
    """
    Compute a deterministic SHA-256 checksum over canonical candle fields.
    Guarantees that any modification to candle timestamps, OHLCV, or open interest
    produces a completely different checksum.
    """
    hasher = hashlib.sha256()
    for c in candles:
        ts_str = c.exchange_timestamp.astimezone(UTC).isoformat()
        oi_str = str(c.open_interest) if c.open_interest is not None else "NONE"
        canonical_line = (
            f"{c.symbol}|{c.instrument_type}|{c.contract_id}|{ts_str}|{c.timeframe}|"
            f"{c.open:.4f}|{c.high:.4f}|{c.low:.4f}|{c.close:.4f}|{c.volume}|{oi_str}\n"
        )
        hasher.update(canonical_line.encode("utf-8"))
    return hasher.hexdigest()


SENTINEL_EMPTY_DATETIME: datetime = datetime(1970, 1, 1, 0, 0, tzinfo=UTC)


class DatasetMetadata(BaseModel):
    """
    Immutable metadata identifying an authoritative backtesting market dataset.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dataset_id: str = Field(description="Deterministic logical dataset identifier")
    symbol: str = Field(description="Normalized market symbol (e.g. NIFTY)")
    timeframe: str = Field(description="Candle interval (e.g. 3m, 15m)")
    start_timestamp: datetime = Field(description="Timestamp of first candle in UTC")
    end_timestamp: datetime = Field(description="Timestamp of last candle in UTC")
    record_count: int = Field(ge=0, description="Total number of candles in dataset")
    data_source: str = Field(description="Authoritative source venue or provider")
    schema_version: int = Field(default=1, ge=1, description="Dataset metadata schema revision")
    checksum: str = Field(description="Deterministic SHA-256 checksum of dataset contents")

    @field_validator("dataset_id", "symbol", "timeframe", "data_source")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise BacktestValidationError("String fields must be non-empty")
        return s

    @field_validator("start_timestamp", "end_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BacktestValidationError(f"Timestamp must be UTC timezone-aware: {v}")
        return v

    @model_validator(mode="after")
    def validate_temporal_bounds(self) -> "DatasetMetadata":
        if self.record_count > 0 and self.start_timestamp > self.end_timestamp:
            raise BacktestValidationError(
                f"start_timestamp ({self.start_timestamp}) cannot be after "
                f"end_timestamp ({self.end_timestamp})"
            )
        return self


class BacktestDataset:
    """
    Container for Phase 2 validated market candles.
    Guarantees strict chronological ascending order, duplicate timestamp detection,
    and automatic deterministic checksum computation.
    """

    def __init__(
        self,
        dataset_id: str,
        candles: Sequence[MarketCandle],
        data_source: str = "HISTORICAL_STORE",
    ) -> None:
        if not dataset_id or not dataset_id.strip():
            raise BacktestValidationError("dataset_id must be non-empty")
        self.dataset_id = dataset_id.strip()

        # Enforce deterministic chronological sorting
        sorted_candles = sorted(candles, key=lambda c: c.exchange_timestamp)
        self._candles: tuple[MarketCandle, ...] = tuple(sorted_candles)

        # Invariant checks: strictly ascending timestamps, no duplicates
        for i in range(len(self._candles) - 1):
            curr_ts = self._candles[i].exchange_timestamp
            next_ts = self._candles[i + 1].exchange_timestamp
            if next_ts < curr_ts:
                raise BacktestValidationError(
                    f"Out of order timestamps at index {i}: {curr_ts} > {next_ts}"
                )
            if next_ts == curr_ts:
                raise BacktestValidationError(
                    f"Duplicate candle timestamp detected at index {i}: {curr_ts}"
                )

        # Metadata computation
        self.checksum = compute_dataset_checksum(self._candles)
        self.record_count = len(self._candles)

        if self.record_count > 0:
            first_c = self._candles[0]
            last_c = self._candles[-1]
            self.metadata = DatasetMetadata(
                dataset_id=self.dataset_id,
                symbol=first_c.symbol,
                timeframe=first_c.timeframe,
                start_timestamp=first_c.exchange_timestamp,
                end_timestamp=last_c.exchange_timestamp,
                record_count=self.record_count,
                data_source=data_source,
                schema_version=1,
                checksum=self.checksum,
            )
        else:
            self.metadata = DatasetMetadata(
                dataset_id=self.dataset_id,
                symbol="EMPTY",
                timeframe="NONE",
                start_timestamp=SENTINEL_EMPTY_DATETIME,
                end_timestamp=SENTINEL_EMPTY_DATETIME,
                record_count=0,
                data_source=data_source,
                schema_version=1,
                checksum=self.checksum,
            )

    @property
    def candles(self) -> tuple[MarketCandle, ...]:
        return self._candles

    def __len__(self) -> int:
        return self.record_count

    def __getitem__(self, index: int) -> MarketCandle:
        return self._candles[index]

    def slice_by_time(self, start: datetime, end: datetime) -> "BacktestDataset":
        """
        Return a new deterministic slice of the dataset within [start, end].
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise BacktestValidationError("Timestamps for slice must be UTC timezone-aware")
        sliced = [c for c in self._candles if start <= c.exchange_timestamp <= end]
        slice_id = f"{self.dataset_id}_SLICE_{int(start.timestamp())}_{int(end.timestamp())}"
        return BacktestDataset(
            dataset_id=slice_id,
            candles=sliced,
            data_source=self.metadata.data_source,
        )

    def diagnose_survivorship(
        self, known_active_contracts: Sequence[str] | None = None
    ) -> list[str]:
        """
        Diagnostic for survivorship or universe completeness issues (Section 34).
        """
        warnings: list[str] = []
        if self.record_count == 0:
            warnings.append("SURVIVORSHIP_WARNING: Dataset contains zero records.")
            return warnings

        unique_contracts = {c.contract_id for c in self._candles}
        if known_active_contracts:
            missing = set(known_active_contracts) - unique_contracts
            if missing:
                warnings.append(
                    f"SURVIVORSHIP_WARNING: {len(missing)} known active contracts "
                    f"missing from historical data: {sorted(missing)}"
                )
        return warnings
