"""
AlphaForge Real-Market Stream Validator (Phase 17 PS-39, PS-40, PS-41).
Validates real-time / replayed NSE market data streams across multi-dimensional contract master
specifications, session boundaries, clock drift, sequence gaps, duplicates, and anomaly detection.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from alphaforge.contract.enums import ContractStatus, SettlementType
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.shadow_validation.enums import DataSourceType, MarketDataAnomalyType
from alphaforge.shadow_validation.market_data_adapter import ProvenanceVerifier
from alphaforge.shadow_validation.models import MarketStreamEvent

if TYPE_CHECKING:
    from typing import Any

    from alphaforge.contract.models import ContractMaster


class MarketStreamValidator:
    """
    Validates streaming market data events for contract correctness, causal timestamps,
    market session boundaries, and anomaly classification.
    """

    # NSE Trading Session: 09:15 to 15:30 IST (03:45 to 10:00 UTC)
    SESSION_START_UTC = time(3, 45)
    SESSION_END_UTC = time(10, 0)
    MAX_CLOCK_DRIFT_SECONDS = 2.0
    STALE_DATA_THRESHOLD_SECONDS = 60.0

    def __init__(self, contract: ContractMaster, expected_data_source: DataSourceType) -> None:
        self._contract = contract
        self._expected_data_source = expected_data_source
        self._last_sequence_no: int | None = None
        self._last_exchange_ts: datetime | None = None
        self._seen_event_ids: set[str] = set()
        self._anomalies_detected: list[dict[str, Any]] = []

    @property
    def contract(self) -> ContractMaster:
        return self._contract

    @property
    def anomalies_detected(self) -> list[dict[str, Any]]:
        return list(self._anomalies_detected)

    def validate_contract_metadata(self) -> bool:
        """Multi-dimensional contract validation (PS-40)."""
        c = self._contract
        if c.exchange != "NSE":
            return False
        if c.segment != "NFO":
            return False
        if c.underlying_symbol != "NIFTY":
            return False
        if c.instrument_type != InstrumentType.FUTURES:
            return False
        if c.status != ContractStatus.ACTIVE:
            return False
        if c.lot_size <= 0 or c.tick_size <= Decimal("0") or c.contract_multiplier <= Decimal("0"):
            return False
        return c.settlement_type == SettlementType.CASH

    def validate_event(self, event: MarketStreamEvent | MarketCandle) -> MarketStreamEvent:
        """Validate a single market stream event and classify anomalies."""
        if not self.validate_contract_metadata():
            msg = (
                f"MarketStreamValidator: Contract {self._contract.contract_id} "
                "failed authoritative metadata validation."
            )
            raise DataIntegrityError(msg)

        now_utc = datetime.now(UTC)
        anomalies: list[MarketDataAnomalyType] = []

        source_type = self._expected_data_source
        if isinstance(event, MarketCandle):
            if self._expected_data_source == DataSourceType.REAL_MARKET_SHADOW:
                anomalies.append(MarketDataAnomalyType.UNVERIFIED_PROVENANCE)
                source_type = DataSourceType.SYNTHETIC

            seq = (self._last_sequence_no + 1) if self._last_sequence_no is not None else 1
            stream_event = MarketStreamEvent(
                event_id=f"EVT-{event.symbol}-{int(event.exchange_timestamp.timestamp())}",
                symbol=event.symbol,
                contract_id=event.contract_id,
                sequence_no=seq,
                exchange_timestamp=event.exchange_timestamp,
                ingestion_timestamp=event.received_timestamp,
                processing_timestamp=now_utc,
                open_price=event.open,
                high_price=event.high,
                low_price=event.low,
                close_price=event.close,
                volume=event.volume,
                data_source=source_type,
                anomalies=[],
            )
        else:
            stream_event = event
            if stream_event.data_source == DataSourceType.REAL_MARKET_SHADOW:
                is_prov_valid, _fail_reason = ProvenanceVerifier.verify_provenance(stream_event)
                if not is_prov_valid:
                    anomalies.append(MarketDataAnomalyType.UNVERIFIED_PROVENANCE)
                    stream_event = stream_event.model_copy(
                        update={"data_source": DataSourceType.SYNTHETIC}
                    )

        # 1. Duplicate event check
        if stream_event.event_id in self._seen_event_ids:
            anomalies.append(MarketDataAnomalyType.DUPLICATE)
        self._seen_event_ids.add(stream_event.event_id)

        # 2. Sequence integrity check
        if self._last_sequence_no is not None:
            if stream_event.sequence_no == self._last_sequence_no:
                if MarketDataAnomalyType.DUPLICATE not in anomalies:
                    anomalies.append(MarketDataAnomalyType.DUPLICATE)
            elif stream_event.sequence_no < self._last_sequence_no:
                anomalies.append(MarketDataAnomalyType.SEQUENCE_REVERSAL)
            elif stream_event.sequence_no > self._last_sequence_no + 1:
                anomalies.append(MarketDataAnomalyType.SEQUENCE_GAP)

        # 3. Timestamp sanity & Clock Drift
        if stream_event.exchange_timestamp > stream_event.ingestion_timestamp:
            drift = (
                stream_event.exchange_timestamp - stream_event.ingestion_timestamp
            ).total_seconds()
            if drift > self.MAX_CLOCK_DRIFT_SECONDS:
                anomalies.append(MarketDataAnomalyType.FUTURE_TIMESTAMP)

        # 4. Out-of-order timestamps
        if (
            self._last_exchange_ts is not None
            and stream_event.exchange_timestamp < self._last_exchange_ts
        ):
            anomalies.append(MarketDataAnomalyType.OUT_OF_ORDER)

        # 5. Stale data check
        if self._last_exchange_ts is not None:
            gap = (stream_event.exchange_timestamp - self._last_exchange_ts).total_seconds()
            if gap > self.STALE_DATA_THRESHOLD_SECONDS:
                anomalies.append(MarketDataAnomalyType.STALE)

        # 6. Session boundary check
        event_time = stream_event.exchange_timestamp.time()
        is_session = self.SESSION_START_UTC <= event_time <= self.SESSION_END_UTC

        # Update state
        self._last_sequence_no = stream_event.sequence_no
        self._last_exchange_ts = stream_event.exchange_timestamp

        for an in anomalies:
            self._anomalies_detected.append(
                {
                    "event_id": stream_event.event_id,
                    "anomaly_type": an.value,
                    "timestamp": stream_event.exchange_timestamp.isoformat(),
                }
            )

        if anomalies:
            return MarketStreamEvent(
                event_id=stream_event.event_id,
                symbol=stream_event.symbol,
                contract_id=stream_event.contract_id,
                sequence_no=stream_event.sequence_no,
                exchange_timestamp=stream_event.exchange_timestamp,
                ingestion_timestamp=stream_event.ingestion_timestamp,
                processing_timestamp=stream_event.processing_timestamp,
                open_price=stream_event.open_price,
                high_price=stream_event.high_price,
                low_price=stream_event.low_price,
                close_price=stream_event.close_price,
                volume=stream_event.volume,
                data_source=stream_event.data_source,
                anomalies=anomalies,
                is_session_valid=is_session,
            )

        return stream_event
