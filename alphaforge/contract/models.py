"""
AlphaForge Futures Contract Master Models.
Enforces strict immutability, Decimal precision, and temporal lifecycle invariants.
"""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.core.exceptions import ContractValidationError
from alphaforge.data.enums import InstrumentType


class ContractMaster(BaseModel):
    """
    Immutable canonical Contract Master model representing a futures instrument.
    Conforms to Master Specification Section 10 and Phase 3 requirements.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    exchange: str = Field(description="Market exchange venue (e.g. NSE, CME)")
    segment: str = Field(description="Market segment (e.g. NFO, COM)")
    underlying_symbol: str = Field(description="Normalized underlying ticker (e.g. NIFTY)")
    contract_id: str = Field(description="Unique contract identifier (e.g. NIFTY26SEPFUT)")
    instrument_type: InstrumentType = Field(
        default=InstrumentType.FUTURES, description="INDEX | FUTURES | EQUITY"
    )
    expiry_datetime: datetime = Field(description="Final settlement / expiry UTC timestamp")
    listing_datetime: datetime = Field(description="Contract creation / listing UTC timestamp")
    trading_start_datetime: datetime = Field(
        description="First permissible trading session UTC timestamp"
    )
    trading_end_datetime: datetime = Field(
        description="Last permissible trading session close UTC timestamp"
    )
    lot_size: int = Field(gt=0, description="Standard market lot size (must be > 0)")
    tick_size: Decimal = Field(
        gt=Decimal("0"), description="Minimum price increment (fixed-point Decimal > 0)"
    )
    contract_multiplier: Decimal = Field(
        default=Decimal("1"),
        gt=Decimal("0"),
        description="Contract value multiplier per index/point (Decimal > 0)",
    )
    price_decimal_places: int = Field(default=2, ge=0, description="Price precision decimal places")
    quantity_decimal_places: int = Field(
        default=0, ge=0, description="Quantity precision decimal places (0 for integers)"
    )
    currency: str = Field(default="INR", description="Settlement currency (e.g. INR, USD)")
    settlement_type: SettlementType = Field(
        default=SettlementType.CASH, description="CASH | PHYSICAL"
    )
    status: ContractStatus = Field(
        default=ContractStatus.ACTIVE, description="Static or base declared status"
    )
    data_source: str = Field(description="Contract provenance or reference feed source")
    contract_version: int = Field(default=1, description="Contract metadata revision number")
    is_suspended: bool = Field(
        default=False, description="True if contract trading is halted/suspended by venue"
    )

    @field_validator("exchange", "segment", "underlying_symbol", "contract_id", "currency")
    @classmethod
    def validate_uppercase_non_empty(cls, v: str) -> str:
        """Enforce uppercase non-empty string convention."""
        clean = v.strip().upper()
        if not clean or clean != v.strip():
            raise ContractValidationError(f"Field must be non-empty and uppercase: '{v}'")
        return clean

    @field_validator(
        "expiry_datetime",
        "listing_datetime",
        "trading_start_datetime",
        "trading_end_datetime",
    )
    @classmethod
    def validate_utc_datetime(cls, v: datetime) -> datetime:
        """Enforce explicit UTC timezone awareness."""
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(v):
            raise ContractValidationError(f"Timestamp must be explicitly timezone-aware UTC: {v}")
        return v

    @model_validator(mode="after")
    def validate_contract_invariants(self) -> "ContractMaster":
        """Validate physical, temporal, and numeric invariants."""
        # 1. Finite Decimal validation
        if not self.tick_size.is_finite() or self.tick_size.is_nan():
            raise ContractValidationError(f"tick_size must be finite: {self.tick_size}")
        if not self.contract_multiplier.is_finite() or self.contract_multiplier.is_nan():
            raise ContractValidationError(
                f"contract_multiplier must be finite: {self.contract_multiplier}"
            )

        # 2. Lot size and tick size positive boundaries
        if self.lot_size <= 0:
            raise ContractValidationError(f"lot_size must be strictly positive: {self.lot_size}")
        if self.tick_size <= Decimal("0"):
            raise ContractValidationError(f"tick_size must be strictly positive: {self.tick_size}")

        # 3. Temporal lifecycle ordering:
        # listing_datetime <= trading_start_datetime < trading_end_datetime <= expiry_datetime
        if self.listing_datetime > self.trading_start_datetime:
            raise ContractValidationError(
                f"listing_datetime {self.listing_datetime} must be <= "
                f"trading_start_datetime {self.trading_start_datetime}"
            )

        if self.trading_start_datetime >= self.trading_end_datetime:
            raise ContractValidationError(
                f"trading_start_datetime {self.trading_start_datetime} must be strictly < "
                f"trading_end_datetime {self.trading_end_datetime}"
            )

        if self.trading_end_datetime > self.expiry_datetime:
            raise ContractValidationError(
                f"trading_end_datetime {self.trading_end_datetime} must be <= "
                f"expiry_datetime {self.expiry_datetime}"
            )

        return self
