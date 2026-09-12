"""
AlphaForge Canonical Serialization and Cryptographic Hashing.
Provides deterministic JSON serialization, SHA-256 event identity derivation,
and event hash calculation for the immutable Audit Ledger.
Guarantees byte-level determinism across platforms and environments.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from alphaforge.core.exceptions import LedgerIntegrityError
from alphaforge.ledger.models import AuditEventType


def canonicalize_value(val: Any) -> Any:
    """
    Recursively transform values into canonical deterministic JSON-encodable types.
    - Decimals serialized as exact fixed-point string (never binary float).
    - Datetimes normalized to timezone-aware UTC ISO 8601 strings.
    - Enums converted to their string values.
    - Dictionaries/Mappings sorted recursively by stringified keys.
    - Lists/tuples preserved in original element order, each element canonicalized.
    """
    if isinstance(val, Decimal):
        # Format as fixed-point string to prevent scientific notation or float conversion
        return f"{val:f}"
    if isinstance(val, datetime):
        if val.tzinfo is None or val.utcoffset() != UTC.utcoffset(val):
            raise LedgerIntegrityError(f"Datetime must be timezone-aware UTC: {val}")
        return val.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, (dict, Mapping)):
        return {
            str(k): canonicalize_value(v)
            for k, v in sorted(val.items(), key=lambda item: str(item[0]))
        }
    if isinstance(val, (list, tuple)):
        return [canonicalize_value(x) for x in val]
    if isinstance(val, (bool, int, str)) or val is None:
        return val

    raise LedgerIntegrityError(
        f"Unsupported value type for canonical serialization: {type(val)} ({val!r})"
    )


def canonical_json(data: Any) -> str:
    """
    Produce canonical, compact UTF-8 JSON string with sorted keys and no extraneous whitespace.
    """
    canonical_data = canonicalize_value(data)
    return json.dumps(
        canonical_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_logical_event_id(
    event_type: AuditEventType | str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: Mapping[str, Any],
    schema_version: int = 1,
) -> str:
    """
    Derive deterministic logical event identifier.
    Guarantees that event_id depends ONLY on logical event identity and payload,
    and is strictly independent of ledger sequence_number or timing metadata.
    Format: EVT-{24_HEX_CHARS}
    """
    event_type_str = (
        event_type.value if isinstance(event_type, AuditEventType) else str(event_type).strip()
    )
    identity_dict = {
        "causation_id": causation_id.strip(),
        "correlation_id": correlation_id.strip(),
        "entity_id": entity_id.strip(),
        "entity_type": entity_type.strip(),
        "event_type": event_type_str,
        "payload": payload,
        "schema_version": schema_version,
    }
    canonical_repr = canonical_json(identity_dict)
    identity_hash = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
    return f"EVT-{identity_hash[:24].upper()}"


def compute_event_hash(
    schema_version: int,
    sequence_number: int,
    event_timestamp: datetime,
    event_type: AuditEventType | str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    causation_id: str,
    payload: Mapping[str, Any],
    previous_event_hash: str,
) -> str:
    """
    Calculate the authoritative SHA-256 event hash for the hash chain.
    Incorporates ledger ordering (sequence_number) and previous_event_hash linkage.
    """
    event_type_str = (
        event_type.value if isinstance(event_type, AuditEventType) else str(event_type).strip()
    )
    hash_payload = {
        "causation_id": causation_id.strip(),
        "correlation_id": correlation_id.strip(),
        "entity_id": entity_id.strip(),
        "entity_type": entity_type.strip(),
        "event_timestamp": event_timestamp,
        "event_type": event_type_str,
        "payload": payload,
        "previous_event_hash": previous_event_hash.strip(),
        "schema_version": schema_version,
        "sequence_number": sequence_number,
    }
    canonical_repr = canonical_json(hash_payload)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest().lower()
