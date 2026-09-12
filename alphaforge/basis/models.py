"""
AlphaForge Index-Futures Basis Data Models.
Enforces strict immutability, Decimal precision, and UTC timestamp governance.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.basis.enums import BasisConfirmationStatus, BasisStatus, BasisZScoreStatus
from alphaforge.contract.enums import ContractStatus
from alphaforge.core.exceptions import BasisCalculationError
from alphaforge.data.enums import DataQualityStatus


class BasisConfig(BaseModel):
    """
    Configuration parameters for basis calculation, timestamp skew, and rolling statistics.
    All values represent deterministic V1 baselines.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_timestamp_skew_seconds: Decimal = Field(
        default=Decimal("60"),
        gt=Decimal("0"),
        description="Maximum permissible temporal skew between index and futures in seconds",
    )
    max_stale_seconds: Decimal = Field(
        default=Decimal("300"),
        gt=Decimal("0"),
        description="Maximum permissible observation age relative to evaluation time in seconds",
    )
    rolling_window_size: int = Field(
        default=20,
        ge=2,
        description="Number of historical basis_pct observations required for rolling statistics",
    )
    z_score_lower_threshold: Decimal = Field(
        default=Decimal("-2.5"),
        description="Z-score lower threshold for LOWER classification",
    )
    z_score_upper_threshold: Decimal = Field(
        default=Decimal("2.5"),
        description="Z-score upper threshold for HIGHER classification",
    )
    calculation_version: int = Field(
        default=1,
        ge=1,
        description="Basis calculation formula and algorithm revision",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "BasisConfig":
        """Verify upper threshold is strictly greater than lower threshold."""
        if self.z_score_upper_threshold <= self.z_score_lower_threshold:
            raise BasisCalculationError(
                f"z_score_upper_threshold {self.z_score_upper_threshold} must be > "
                f"z_score_lower_threshold {self.z_score_lower_threshold}"
            )
        return self


class BasisObservation(BaseModel):
    """
    Immutable canonical basis observation between an index reference and a futures contract.
    All prices and basis measurements use fixed-point Decimal arithmetic.
    Units:
      - index_price: price points (Decimal)
      - futures_price: price points (Decimal)
      - basis: absolute price points difference (futures_price - index_price)
      - basis_pct: normalized percentage / ratio ((futures_price - index_price) / index_price)
      - timestamp_skew_seconds: seconds difference (abs(futures_timestamp - index_timestamp))
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    underlying_symbol: str = Field(description="Canonical underlying symbol (e.g. NIFTY)")
    index_contract_id: str = Field(description="Identifier for index reference (e.g. NIFTY-SPOT)")
    futures_contract_id: str = Field(
        description="Identifier for futures contract (e.g. NIFTY26JUNFUT)"
    )
    index_price: Decimal = Field(description="Index / spot reference price in points (Decimal)")
    futures_price: Decimal = Field(description="Futures contract price in points (Decimal)")
    basis: Decimal = Field(description="Absolute basis in points (futures_price - index_price)")
    basis_pct: Decimal = Field(
        description="Normalized basis ratio: (futures_price - index_price) / index_price"
    )
    index_timestamp: datetime = Field(description="Index exchange observation UTC timestamp")
    futures_timestamp: datetime = Field(description="Futures exchange observation UTC timestamp")
    evaluation_timestamp: datetime = Field(description="Evaluation / calculation UTC timestamp")
    timestamp_skew_seconds: Decimal = Field(
        ge=Decimal("0"),
        description="Temporal difference between index and futures timestamps in seconds",
    )
    data_quality: DataQualityStatus = Field(
        description="Aggregate or worst data quality of underlying source feeds"
    )
    contract_status: ContractStatus = Field(
        description="Lifecycle status of futures contract at evaluation timestamp"
    )
    basis_status: BasisStatus = Field(
        description="Evaluation status (VALID, MISALIGNED_TIMESTAMP, STALE, etc.)"
    )
    calculation_version: int = Field(default=1, description="Basis calculation engine revision")

    @field_validator("underlying_symbol", "index_contract_id", "futures_contract_id")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        """Enforce uppercase non-empty string convention."""
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise BasisCalculationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator("index_timestamp", "futures_timestamp", "evaluation_timestamp")
    @classmethod
    def validate_utc_datetime(cls, v: datetime) -> datetime:
        """Enforce explicit UTC timezone awareness."""
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise BasisCalculationError(f"Timestamp must be explicitly timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_numerics(self) -> "BasisObservation":
        """Validate numeric finiteness."""
        for field_name in (
            "index_price",
            "futures_price",
            "basis",
            "basis_pct",
            "timestamp_skew_seconds",
        ):
            val: Decimal = getattr(self, field_name)
            if not val.is_finite() or val.is_nan():
                raise BasisCalculationError(f"{field_name} must be finite Decimal: {val}")
        return self


class RollingBasisStats(BaseModel):
    """
    Deterministic rolling statistics over historical basis_pct observations.
    Units:
      - rolling_mean: normalized ratio (Decimal)
      - rolling_std: sample standard deviation of normalized ratio (Decimal)
      - z_score: dimensionless standard score (Decimal)
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    count: int = Field(ge=0, description="Number of observations in sample")
    window_size: int = Field(ge=2, description="Target window size (e.g. 20)")
    rolling_mean: Decimal | None = Field(default=None, description="Rolling mean of basis_pct")
    rolling_std: Decimal | None = Field(
        default=None, description="Rolling sample standard deviation of basis_pct"
    )
    z_score: Decimal | None = Field(
        default=None,
        description="Standard score: (current_basis_pct - rolling_mean) / rolling_std",
    )
    z_score_status: BasisZScoreStatus = Field(
        default=BasisZScoreStatus.UNDEFINED,
        description="Classification: LOWER, NORMAL, HIGHER, or UNDEFINED",
    )


class BasisConfirmationResult(BaseModel):
    """
    Deterministic confirmation gate result for index-futures basis.
    Supplies structural confirmation state to downstream callers; does NOT execute orders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: BasisConfirmationStatus = Field(
        description="Confirmation state: CONFIRMED, NOT_CONFIRMED, INVALID"
    )
    observation: BasisObservation | None = Field(
        default=None, description="Current basis observation if valid"
    )
    rolling_stats: RollingBasisStats | None = Field(
        default=None, description="Rolling statistics if calculated"
    )
    reason: str = Field(default="", description="Human-readable explanation of confirmation state")
