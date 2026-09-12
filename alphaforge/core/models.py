"""
AlphaForge Core Immutable Data Models.
All models enforce strict immutability (frozen=True) and fixed-point Decimal arithmetic for prices.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.exceptions import DataIntegrityError


class Candle(BaseModel):
    """
    Immutable representation of an OHLCV market candle.
    Enforces strict mathematical boundary invariants.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_interest: int | None = None
    is_closed: bool = True

    @model_validator(mode="after")
    def validate_ohlc_invariants(self) -> "Candle":
        """Validate price boundaries and volume non-negativity."""
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
        if self.volume < 0:
            raise DataIntegrityError(f"Volume must be non-negative: volume={self.volume}")
        if self.open_interest is not None and self.open_interest < 0:
            raise DataIntegrityError(
                f"Open interest must be non-negative: open_interest={self.open_interest}"
            )
        return self


class StrategySignal(BaseModel):
    """
    Immutable signal artifact emitted by the Strategy Engine.
    Conforms strictly to Master Specification Section 15.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    signal_id: str = Field(description="Deterministic SHA-256 hash identifier")
    strategy_id: str = Field(description="Stable strategy identifier")
    strategy_version: str = Field(description="Semantic version of strategy code")
    symbol: str = Field(description="Trading symbol, e.g. NIFTY")
    direction: SignalDirection = Field(description="LONG | SHORT | FLAT")
    signal_timestamp: datetime = Field(description="Close timestamp of trigger candle [1]")
    evaluation_timestamp: datetime = Field(description="Evaluation context timestamp")
    entry_reference: Decimal = Field(description="Deterministic entry reference price")
    stop_reference: Decimal = Field(description="Deterministic stop-loss reference price")
    target_reference: Decimal = Field(description="Deterministic profit target reference price")
    risk_distance: Decimal = Field(description="abs(entry_reference - stop_reference)")
    trend_state: TrendState = Field(description="Higher-timeframe trend regime")
    basis_status: FuturesConfirmationStatus = Field(description="Status of futures confirmation")
    volume_status: str = Field(description="Volume confirmation status")
    decision: StrategyDecision = Field(
        description="ACCEPT | REJECT | INVALID_DATA | EXPIRED | DUPLICATE"
    )
    rejection_code: RejectionCode = Field(description="Formal machine-readable rejection code")
    config_hash: str = Field(description="SHA-256 hash of canonical strategy configuration")
    data_version: int = Field(default=1, description="Schema revision number")
