"""
AlphaForge Market Data Enumerations.
Enforces strict string representations for deterministic serialization and auditability.
"""

from collections.abc import Iterable
from enum import StrEnum


class DataQualityStatus(StrEnum):
    """
    Formal data quality classifications adhering to Phase 2 Data Quality Model.
    """

    VALID = "VALID"
    EMPTY = "EMPTY"
    INVALID = "INVALID"
    STALE = "STALE"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    CONFLICT = "CONFLICT"
    INCOMPLETE = "INCOMPLETE"


class Timeframe(StrEnum):
    """Supported market data candle intervals."""

    TF_1M = "1m"
    TF_3M = "3m"
    TF_5M = "5m"
    TF_15M = "15m"
    TF_1H = "1h"
    TF_1D = "1d"


class InstrumentType(StrEnum):
    """Classification of traded instruments."""

    INDEX = "INDEX"
    FUTURES = "FUTURES"
    EQUITY = "EQUITY"


# Deterministic defect precedence hierarchy:
# INVALID > CONFLICT > OUT_OF_ORDER > DUPLICATE > GAP > STALE > INCOMPLETE > EMPTY > VALID
QUALITY_PRECEDENCE_RANK: dict[DataQualityStatus, int] = {
    DataQualityStatus.INVALID: 1,
    DataQualityStatus.CONFLICT: 2,
    DataQualityStatus.OUT_OF_ORDER: 3,
    DataQualityStatus.DUPLICATE: 4,
    DataQualityStatus.GAP: 5,
    DataQualityStatus.STALE: 6,
    DataQualityStatus.INCOMPLETE: 7,
    DataQualityStatus.EMPTY: 8,
    DataQualityStatus.VALID: 9,
}


def most_severe_status(statuses: Iterable[DataQualityStatus]) -> DataQualityStatus:
    """
    Determine the most severe data quality status according to the precedence hierarchy.
    If statuses is empty, returns DataQualityStatus.EMPTY.
    """
    status_list = list(statuses)
    if not status_list:
        return DataQualityStatus.EMPTY
    return min(status_list, key=lambda s: QUALITY_PRECEDENCE_RANK[s])
