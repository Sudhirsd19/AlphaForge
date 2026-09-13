"""
AlphaForge Fault Injection Safety Invariants.
Reusable assertion functions that verify critical safety properties
hold after fault injection and recovery.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from alphaforge.execution.state_machine import TERMINAL_STATES
from alphaforge.ledger.models import AuditEvent
from alphaforge.ledger.serialization import compute_event_hash


def _extract_field(item: Any, keys: Sequence[str], default: Any = None) -> Any:
    """Extract a field from dict, AuditEvent payload, or object attribute."""
    payload: Mapping[str, Any] | None = None
    if hasattr(item, "payload") and isinstance(item.payload, Mapping):
        payload = item.payload
    for k in keys:
        if isinstance(item, Mapping) and k in item and item[k] is not None:
            return item[k]
        if payload is not None and k in payload and payload[k] is not None:
            return payload[k]
        if hasattr(item, k):
            v = getattr(item, k)
            if v is not None:
                return v
    return default


def assert_no_duplicate_orders(
    orders: Sequence[str | Any],
) -> None:
    """Verify no duplicate order IDs exist."""
    seen: set[str] = set()
    for item in orders:
        if isinstance(item, str):
            oid = item.strip().upper()
        else:
            oid = (
                str(
                    _extract_field(
                        item, ["order_id", "client_order_id", "broker_order_id", "entity_id"], ""
                    )
                )
                .strip()
                .upper()
            )
        if not oid:
            continue
        if oid in seen:
            raise AssertionError(f"Duplicate order detected: {oid}")
        seen.add(oid)


def _get_record_field(
    item: Any,
    payload: Mapping[str, Any] | None,
    key: str,
    default: Any = None,
) -> Any:
    """Extract a field from mapping, payload mapping, or object attribute."""
    if isinstance(item, Mapping):
        val = item.get(key)
        if val is not None:
            return val
    if payload is not None and key in payload:
        val = payload.get(key)
        if val is not None:
            return val
    return getattr(item, key, default)


def assert_no_duplicate_fills(
    fill_records: Sequence[dict[str, Any] | Any],
) -> None:
    """
    Verify no duplicate logical fill events exist.

    Logical fill identity is derived hierarchically:
    1. Explicit immutable 'fill_id' if available and non-empty.
    2. Otherwise, deterministic composite identity:
       (order_id, fill_quantity, fill_price, execution_timestamp, partial_fill_index)

    Detects duplicate logical fills across different ledger sequences,
    while correctly distinguishing legitimate distinct partial fills.
    """
    seen: set[tuple[Any, ...]] = set()
    for item in fill_records:
        payload: Mapping[str, Any] | None = None
        if hasattr(item, "payload") and isinstance(item.payload, Mapping):
            payload = item.payload

        fill_id = _get_record_field(item, payload, "fill_id")
        order_id = (
            _get_record_field(item, payload, "order_id")
            or _get_record_field(item, payload, "client_order_id")
            or _get_record_field(item, payload, "entity_id")
            or ""
        )
        fill_qty = (
            _get_record_field(item, payload, "filled_quantity")
            if _get_record_field(item, payload, "filled_quantity") is not None
            else _get_record_field(
                item, payload, "fill_qty", _get_record_field(item, payload, "quantity")
            )
        )
        fill_price = (
            _get_record_field(item, payload, "average_price")
            if _get_record_field(item, payload, "average_price") is not None
            else _get_record_field(
                item, payload, "fill_price", _get_record_field(item, payload, "price")
            )
        )
        ts = (
            _get_record_field(item, payload, "timestamp")
            or _get_record_field(item, payload, "event_timestamp")
            or _get_record_field(item, payload, "filled_at")
        )
        partial_idx = (
            _get_record_field(item, payload, "fill_index")
            if _get_record_field(item, payload, "fill_index") is not None
            else _get_record_field(
                item, payload, "partial_id", _get_record_field(item, payload, "remaining_quantity")
            )
        )

        if fill_id is not None and str(fill_id).strip():
            key: tuple[Any, ...] = ("FILL_ID", str(fill_id).strip().upper())
        else:
            norm_price = str(Decimal(str(fill_price))) if fill_price is not None else None
            norm_qty = int(fill_qty) if fill_qty is not None else None
            norm_ts = str(ts) if ts is not None else None
            norm_order_id = str(order_id).strip().upper()
            key = ("COMPOSITE", norm_order_id, norm_qty, norm_price, norm_ts, partial_idx)

        if key in seen:
            raise AssertionError(f"Duplicate fill detected for order '{order_id}': identity={key}")
        seen.add(key)


def _normalize_side(raw: Any) -> str:
    """Normalize BUY/LONG -> LONG and SELL/SHORT -> SHORT."""
    if not raw:
        return ""
    val = str(raw.value if hasattr(raw, "value") else raw).strip().upper()
    if val in ("BUY", "LONG"):
        return "LONG"
    if val in ("SELL", "SHORT"):
        return "SHORT"
    return val


def assert_no_phantom_positions(
    positions: Sequence[Any],
    authoritative_executions: Sequence[Any],
) -> None:
    """
    Verify every non-zero position has explicit authoritative execution provenance.

    A positive position MUST have an explicit authoritative provenance path:
      1. POSITION -> origin_order_id / client_order_id -> authoritative execution/fill
      2. POSITION -> explicit position_id / position reference -> authoritative execution/fill

    Provenance is NEVER inferred solely from market attributes (symbol + side).
    Executions from different orders are NEVER aggregated merely because they
    share the same symbol and side.
    """
    parsed_executions: list[dict[str, Any]] = []
    for ex in authoritative_executions:
        client_id = str(_extract_field(ex, ["client_order_id"], "")).strip().upper()
        broker_id = str(_extract_field(ex, ["broker_order_id"], "")).strip().upper()
        order_id = str(_extract_field(ex, ["order_id", "entity_id"], "")).strip().upper()
        ids = {i for i in (client_id, broker_id, order_id) if i}

        ex_sym = str(_extract_field(ex, ["symbol", "instrument"], "")).strip().upper()
        raw_side = _extract_field(ex, ["side", "direction"], "")
        ex_side = _normalize_side(raw_side)
        ex_qty = int(_extract_field(ex, ["filled_quantity", "fill_qty", "quantity", "qty"], 0))
        pos_ref = _extract_field(
            ex, ["position_id", "position_ref", "pos_ref", "origin_position_id"]
        )
        pos_ref_clean = str(pos_ref).strip().upper() if pos_ref else None

        parsed_executions.append(
            {
                "ids": ids,
                "symbol": ex_sym,
                "side": ex_side,
                "qty": ex_qty,
                "pos_ref": pos_ref_clean,
            }
        )

    for pos in positions:
        pos_id = str(_extract_field(pos, ["position_id", "id", "entity_id"], "")).strip().upper()
        pos_sym = str(_extract_field(pos, ["symbol", "instrument"], "")).strip().upper()
        raw_side = _extract_field(pos, ["side", "direction"], "")
        pos_side = _normalize_side(raw_side)
        pos_qty = int(_extract_field(pos, ["quantity", "qty", "size"], 0))
        origin_order_id = _extract_field(
            pos, ["origin_order_id", "client_order_id", "order_id", "originating_order_id"]
        )

        if pos_qty <= 0:
            continue

        clean_origin = str(origin_order_id).strip().upper() if origin_order_id else ""
        matching: list[dict[str, Any]] = []

        if clean_origin:
            # 1. Provenance via origin_order_id / client_order_id
            matching = [ex for ex in parsed_executions if clean_origin in ex["ids"]]
            if not matching:
                raise AssertionError(
                    f"Phantom position detected: position '{pos_id or clean_origin}' references "
                    f"non-existent order '{origin_order_id}'"
                )
        elif pos_id:
            # 2. Provenance via explicit position reference on executions
            matching = [ex for ex in parsed_executions if ex["pos_ref"] == pos_id]
            if not matching:
                raise AssertionError(
                    f"Position '{pos_id}' has no explicit authoritative execution provenance"
                )
        else:
            # Fail closed when non-zero position has no provenance identifiers
            raise AssertionError("Position has no explicit authoritative execution provenance")

        for m in matching:
            if m["symbol"] and m["symbol"] != pos_sym:
                raise AssertionError(
                    f"Provenance symbol mismatch for position '{pos_id or clean_origin}': "
                    f"position symbol '{pos_sym}' != execution symbol '{m['symbol']}'"
                )
            if m["side"] and m["side"] != pos_side:
                raise AssertionError(
                    f"Provenance side mismatch for position '{pos_id or clean_origin}': "
                    f"position side '{pos_side}' != execution side '{m['side']}'"
                )

        supported_qty = sum(m["qty"] for m in matching)
        if pos_qty > supported_qty:
            raise AssertionError(
                f"Position quantity ({pos_qty}) exceeds supported executed quantity "
                f"({supported_qty}) for position '{pos_id or clean_origin}'"
            )


def assert_no_double_pnl(
    trade_pnls: Sequence[tuple[str, Decimal] | dict[str, Any] | Any],
) -> None:
    """Verify each trade ID appears at most once in PnL records."""
    seen: set[str] = set()
    for item in trade_pnls:
        if isinstance(item, tuple):
            trade_id = str(item[0]).strip().upper()
        elif isinstance(item, Mapping):
            trade_id = str(item.get("trade_id", item.get("id", ""))).strip().upper()
        else:
            trade_id = str(getattr(item, "trade_id", getattr(item, "id", ""))).strip().upper()
        if not trade_id:
            continue
        if trade_id in seen:
            raise AssertionError(f"Double PnL detected for trade: {trade_id}")
        seen.add(trade_id)


def assert_valid_fsm_history(
    transitions: Sequence[tuple[str, str]],
    allowed: dict[str, frozenset[str]] | None = None,
) -> None:
    """Verify every (from_state, to_state) pair is legal."""
    from alphaforge.execution.state_machine import (
        ALLOWED_TRANSITIONS,
    )

    matrix = allowed or {
        k.value: frozenset(s.value for s in v) for k, v in ALLOWED_TRANSITIONS.items()
    }
    for from_s, to_s in transitions:
        allowed_targets = matrix.get(from_s, frozenset())
        if to_s not in allowed_targets:
            raise AssertionError(f"Illegal FSM transition: {from_s} -> {to_s}")


def assert_hash_chain_intact(events: Sequence[AuditEvent]) -> None:
    """Verify the cryptographic hash chain and event hash integrity."""
    for i in range(1, len(events)):
        prev = events[i - 1]
        curr = events[i]
        if curr.previous_event_hash != prev.event_hash:
            raise AssertionError(
                f"Hash chain broken at sequence "
                f"{curr.sequence_number}: "
                f"prev_hash={curr.previous_event_hash} != "
                f"expected={prev.event_hash}"
            )
        if curr.sequence_number != prev.sequence_number + 1:
            raise AssertionError(f"Sequence gap: {prev.sequence_number} -> {curr.sequence_number}")

    for curr in events:
        recomputed = compute_event_hash(
            schema_version=curr.schema_version,
            sequence_number=curr.sequence_number,
            event_timestamp=curr.event_timestamp,
            event_type=curr.event_type,
            entity_type=curr.entity_type,
            entity_id=curr.entity_id,
            correlation_id=curr.correlation_id,
            causation_id=curr.causation_id,
            payload=dict(curr.payload),
            previous_event_hash=curr.previous_event_hash,
        )
        if recomputed != curr.event_hash:
            raise AssertionError(
                f"Hash chain corruption: event hash mismatch at sequence {curr.sequence_number}: "
                f"recorded={curr.event_hash} != computed={recomputed}"
            )


def assert_risk_limits_enforced(
    open_trade_count: int,
    max_open_trades: int,
    reserved_risk: Decimal,
    max_portfolio_risk_amount: Decimal,
) -> None:
    """Verify risk limits have not been silently bypassed."""
    if open_trade_count > max_open_trades:
        raise AssertionError(
            f"Risk limit bypassed: open_trades={open_trade_count} > max={max_open_trades}"
        )
    if reserved_risk > max_portfolio_risk_amount:
        raise AssertionError(
            f"Risk limit bypassed: reserved_risk={reserved_risk} > max={max_portfolio_risk_amount}"
        )


def assert_no_fabricated_data(
    data_source: str,
    values: Sequence[Any],
) -> None:
    """Verify no values were fabricated (e.g. 'UNKNOWN', None defaults)."""
    for val in values:
        if val in ("UNKNOWN", "FABRICATED", None):
            raise AssertionError(f"Fabricated data detected in {data_source}: {val}")


def assert_terminal_state_immutable(
    state: str,
    attempted_target: str,
) -> None:
    """Verify terminal states cannot be resurrected."""
    terminal_values = {s.value for s in TERMINAL_STATES}
    if state in terminal_values:
        raise AssertionError(
            f"Terminal state resurrection attempted: {state} -> {attempted_target}"
        )


def assert_sequence_contiguous(
    sequence_numbers: Sequence[int],
) -> None:
    """Verify sequence numbers are strictly contiguous (1, 2, 3, ...)."""
    for i in range(1, len(sequence_numbers)):
        if sequence_numbers[i] != sequence_numbers[i - 1] + 1:
            raise AssertionError(
                f"Sequence gap: {sequence_numbers[i - 1]} -> {sequence_numbers[i]}"
            )
