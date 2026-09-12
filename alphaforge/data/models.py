"""
AlphaForge Market Data Models.
Enforces strict immutability, Decimal precision, UTC awareness, and mathematical OHLC boundaries.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.core.models import Candle
from alphaforge.data.enums import DataQualityStatus, InstrumentType


class MarketCandle(BaseModel):
    """
    Canonical 15-field immutable market data candle conforming to
    Master Specification Section 9.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    symbol: str = Field(description="Normalized ticker symbol (e.g. NIFTY)")
    instrument_type: InstrumentType = Field(description="INDEX | FUTURES | EQUITY")
    contract_id: str = Field(
        description="Unique contract identifier (e.g. NIFTY-SPOT, NIFTY26SEPFUT)"
    )
    exchange_timestamp: datetime = Field(description="Candle timestamp from exchange in UTC")
    received_timestamp: datetime = Field(description="Ingestion arrival timestamp in UTC")
    timeframe: str = Field(description="Candle duration (e.g. 3m, 15m)")
    open: Decimal = Field(description="Opening price")
    high: Decimal = Field(description="Highest price")
    low: Decimal = Field(description="Lowest price")
    close: Decimal = Field(description="Closing price")
    volume: int = Field(description="Total traded volume (>= 0)")
    open_interest: int | None = Field(default=None, description="Open interest if available (>= 0)")
    source: str = Field(description="Data provider or venue source tag")
    quality_status: DataQualityStatus = Field(
        default=DataQualityStatus.VALID, description="Data quality classification"
    )
    data_version: int = Field(default=1, description="Schema revision number")
    is_closed: bool = Field(default=True, description="True if bar is closed, False if forming [0]")

    @field_validator("symbol")
    @classmethod
    def validate_symbol_uppercase(cls, v: str) -> str:
        """Enforce uppercase symbol naming convention."""
        if not v or v != v.upper().strip():
            raise DataIntegrityError(f"Symbol must be non-empty and uppercase: '{v}'")
        return v

    @field_validator("exchange_timestamp", "received_timestamp")
    @classmethod
    def validate_utc_timezone(cls, v: datetime) -> datetime:
        """Enforce explicit UTC timezone awareness."""
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise DataIntegrityError(f"Timestamp must be timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_market_candle_invariants(self) -> "MarketCandle":
        """Enforce physical, temporal, and mathematical OHLC invariants."""
        # 1. Price boundaries
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise DataIntegrityError(
                f"Candle prices must be strictly positive: open={self.open}, high={self.high}, "
                f"low={self.low}, close={self.close}"
            )
        if self.high < max(self.open, self.close, self.low):
            max_p = max(self.open, self.close, self.low)
            raise DataIntegrityError(
                f"High price {self.high} must be >= max(open, close, low)={max_p}"
            )
        if self.low > min(self.open, self.close, self.high):
            min_p = min(self.open, self.close, self.high)
            raise DataIntegrityError(
                f"Low price {self.low} must be <= min(open, close, high)={min_p}"
            )

        # 2. Volume & Open Interest non-negativity
        if self.volume < 0:
            raise DataIntegrityError(f"Volume must be non-negative: volume={self.volume}")
        if self.open_interest is not None and self.open_interest < 0:
            raise DataIntegrityError(
                f"Open interest must be non-negative: open_interest={self.open_interest}"
            )

        # 3. Temporal causality: received_timestamp cannot precede exchange_timestamp
        if self.received_timestamp < self.exchange_timestamp:
            raise DataIntegrityError(
                f"Causality violation: received_timestamp {self.received_timestamp} "
                f"is earlier than exchange_timestamp {self.exchange_timestamp}"
            )

        return self

    def to_strategy_candle(self) -> Candle:
        """
        Bridge method mapping canonical MarketCandle to Phase 1 Strategy Candle.
        Guarantees exact field compatibility with Strategy Engine.
        """
        return Candle(
            timestamp=self.exchange_timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            open_interest=self.open_interest,
            is_closed=self.is_closed,
        )


class RawMarketRecord(BaseModel):
    """
    Ingestion schema for unnormalized raw market records before validation and canonicalization.
    """

    model_config = ConfigDict(extra="ignore")

    symbol: str
    instrument_type: str
    contract_id: str
    exchange_timestamp: str | datetime
    received_timestamp: str | datetime
    timeframe: str
    open: str | int | float | Decimal
    high: str | int | float | Decimal
    low: str | int | float | Decimal
    close: str | int | float | Decimal
    volume: int
    open_interest: int | None = None
    source: str = "UNKNOWN"
    is_closed: bool = True
