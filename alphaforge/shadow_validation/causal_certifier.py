"""
AlphaForge Causal Certifier (Phase 17 PS-49, Correction 1).
Enforces the primary anti-lookahead invariant:
    decision_ts >= max(all_information_timestamps_used_by_decision)
and validates causal timestamp tiering across market events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alphaforge.shadow_validation.enums import MarketDataAnomalyType

if TYPE_CHECKING:
    from datetime import datetime

    from alphaforge.shadow_validation.models import ShadowSignalRecord


class CausalCertifier:
    """
    Certifies that no future market event, candle, tick, volume, indicator,
    or execution result influences an earlier strategy/risk decision.
    """

    def __init__(self) -> None:
        self._violations: list[dict[str, Any]] = []

    @property
    def violations(self) -> list[dict[str, Any]]:
        return list(self._violations)

    def certify_decision(
        self,
        decision_id: str,
        decision_ts: datetime,
        input_event_timestamps: list[datetime],
        derived_indicator_timestamps: list[datetime] | None = None,
        risk_input_timestamps: list[datetime] | None = None,
        contract_metadata_ts: datetime | None = None,
    ) -> bool:
        """
        Enforce decision_ts >= max(all_information_timestamps_used_by_decision).
        Returns True if causal invariant holds, otherwise records violation and returns False.
        """
        all_ts: list[datetime] = list(input_event_timestamps)
        if derived_indicator_timestamps:
            all_ts.extend(derived_indicator_timestamps)
        if risk_input_timestamps:
            all_ts.extend(risk_input_timestamps)
        if contract_metadata_ts:
            all_ts.append(contract_metadata_ts)

        if not all_ts:
            return True

        max_input_ts = max(all_ts)
        if decision_ts < max_input_ts:
            violation = {
                "decision_id": decision_id,
                "decision_ts": decision_ts.isoformat(),
                "max_input_ts": max_input_ts.isoformat(),
                "delta_seconds": (max_input_ts - decision_ts).total_seconds(),
                "error": (
                    "LOOKAHEAD_VIOLATION: Decision timestamp is earlier "
                    "than consumed input data timestamp."
                ),
            }
            self._violations.append(violation)
            return False

        return True

    def certify_signal_record(self, signal: ShadowSignalRecord) -> bool:
        """Certify an immutable ShadowSignalRecord."""
        return self.certify_decision(
            decision_id=signal.decision_id,
            decision_ts=signal.decision_timestamp,
            input_event_timestamps=signal.input_event_timestamps,
        )

    def validate_causal_tiering(
        self,
        exchange_ts: datetime,
        ingestion_ts: datetime,
        processing_ts: datetime,
        decision_ts: datetime,
    ) -> tuple[bool, list[MarketDataAnomalyType]]:
        """Validate timestamp tiering ordering where applicable."""
        anomalies: list[MarketDataAnomalyType] = []

        if exchange_ts > ingestion_ts:
            anomalies.append(MarketDataAnomalyType.FUTURE_TIMESTAMP)
        if ingestion_ts > processing_ts:
            anomalies.append(MarketDataAnomalyType.OUT_OF_ORDER)
        if processing_ts > decision_ts:
            anomalies.append(MarketDataAnomalyType.OUT_OF_ORDER)

        is_valid = len(anomalies) == 0
        return is_valid, anomalies
