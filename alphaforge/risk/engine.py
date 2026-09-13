"""
AlphaForge Deterministic Risk Engine V1.
Implements pre-trade risk evaluation, portfolio limits, daily loss controls,
correlated group exposure, atomic risk reservation, and idempotency.
"""

import threading
from decimal import Decimal

from alphaforge.core.exceptions import RiskValidationError
from alphaforge.risk.calculator import (
    calculate_daily_loss,
    calculate_notional,
    calculate_required_capital,
    calculate_risk_distance,
    calculate_risk_per_unit,
    calculate_stop_distance_pct,
)
from alphaforge.risk.enums import RiskDecisionState, RiskReasonCode, TradeSide
from alphaforge.risk.models import (
    PortfolioRiskState,
    RiskConfig,
    RiskDecision,
    RiskInput,
    RiskReservation,
)


def _reservations_match(a: RiskReservation, b: RiskReservation) -> bool:
    """Return True if two RiskReservations represent identical data."""
    return (
        a.reservation_id == b.reservation_id
        and a.signal_id == b.signal_id
        and a.symbol == b.symbol
        and a.side == b.side
        and a.entry_price == b.entry_price
        and a.stop_price == b.stop_price
        and a.quantity == b.quantity
        and a.monetary_risk == b.monetary_risk
        and a.notional == b.notional
    )


def reconcile_and_deduplicate_reservations(
    sources: tuple[RiskReservation, ...] | list[RiskReservation],
) -> tuple[RiskReservation, ...]:
    """
    Reconcile and deduplicate risk reservations from authoritative and snapshot sources.

    Invariants:
    1. Every reservation must have valid identity (non-empty uppercase IDs).
    2. Identical reservation_id or signal_id must have bit-for-bit identical attributes.
    3. Conflicting attributes for the same ID raise RiskValidationError (fail closed).
    4. Deduplication ensures each logical reservation is included exactly once.
    """
    res_by_id: dict[str, RiskReservation] = {}
    res_by_sig: dict[str, str] = {}

    for r in sources:
        # Identity and numerical validation
        if not r.reservation_id or not r.reservation_id.strip():
            raise RiskValidationError("Reservation missing reservation_id")
        if not r.signal_id or not r.signal_id.strip():
            raise RiskValidationError(f"Reservation '{r.reservation_id}' missing signal_id")
        if not r.symbol or not r.symbol.strip():
            raise RiskValidationError(f"Reservation '{r.reservation_id}' missing symbol")
        if not r.monetary_risk.is_finite() or r.monetary_risk <= Decimal("0"):
            raise RiskValidationError(
                f"Reservation '{r.reservation_id}' has invalid monetary_risk: {r.monetary_risk}"
            )
        if not r.notional.is_finite() or r.notional <= Decimal("0"):
            raise RiskValidationError(
                f"Reservation '{r.reservation_id}' has invalid notional: {r.notional}"
            )
        if r.quantity <= 0:
            raise RiskValidationError(
                f"Reservation '{r.reservation_id}' has non-positive quantity: {r.quantity}"
            )

        # Collision on reservation_id
        if r.reservation_id in res_by_id:
            existing = res_by_id[r.reservation_id]
            if not _reservations_match(existing, r):
                raise RiskValidationError(
                    f"Conflicting reservation data for reservation_id '{r.reservation_id}'"
                )
            continue

        # Collision on signal_id
        if r.signal_id in res_by_sig:
            existing_res_id = res_by_sig[r.signal_id]
            existing = res_by_id[existing_res_id]
            if not _reservations_match(existing, r):
                raise RiskValidationError(
                    f"Conflicting reservation data for signal_id '{r.signal_id}'"
                )
            continue

        res_by_id[r.reservation_id] = r
        res_by_sig[r.signal_id] = r.reservation_id

    return tuple(res_by_id.values())


def _evaluate_trade_risk_internal(
    trade_input: RiskInput,
    portfolio_state: PortfolioRiskState,
    config: RiskConfig | None = None,
) -> RiskDecision:
    """
    Pure deterministic pre-trade risk validation gate.
    Evaluates proposed trade against per-trade, single-position, portfolio,
    daily loss, and correlated exposure risk limits.
    """
    cfg = config or RiskConfig()
    eval_ts = trade_input.evaluation_timestamp

    def _reject(
        code: RiskReasonCode,
        reason: str,
        equity: Decimal | None = None,
        entry: Decimal | None = None,
        stop: Decimal | None = None,
        risk_dist: Decimal | None = None,
        risk_amt: Decimal | None = None,
        qty: int | None = None,
        notional: Decimal | None = None,
        risk_before: Decimal | None = None,
        risk_after: Decimal | None = None,
        loss: Decimal | None = None,
    ) -> RiskDecision:
        return RiskDecision(
            decision=RiskDecisionState.REJECTED,
            reason_code=code,
            reason=reason,
            signal_id=trade_input.signal_id,
            symbol=trade_input.symbol,
            side=trade_input.side,
            equity=equity,
            entry_price=entry,
            stop_price=stop,
            risk_distance=risk_dist,
            risk_amount=risk_amt,
            quantity=qty,
            notional=notional,
            portfolio_risk_before=risk_before,
            portfolio_risk_after=risk_after,
            daily_loss=loss,
            calculation_version=cfg.calculation_version,
            timestamp=eval_ts,
        )

    def _invalid(code: RiskReasonCode, reason: str) -> RiskDecision:
        return RiskDecision(
            decision=RiskDecisionState.INVALID,
            reason_code=code,
            reason=reason,
            signal_id=trade_input.signal_id,
            symbol=trade_input.symbol,
            side=trade_input.side,
            calculation_version=cfg.calculation_version,
            timestamp=eval_ts,
        )

    # 0. Validate and Reconcile Active Reservations in Portfolio State
    try:
        deduped_reservations = reconcile_and_deduplicate_reservations(
            portfolio_state.active_reservations
        )
    except RiskValidationError as err:
        return _invalid(
            RiskReasonCode.UNKNOWN_RISK_STATE,
            f"Inconsistent active_reservations in portfolio_state: {err}",
        )

    # Idempotency check: Same signal_id cannot reserve twice
    for r in deduped_reservations:
        if r.signal_id == trade_input.signal_id:
            return RiskDecision(
                decision=RiskDecisionState.REJECTED,
                reason_code=RiskReasonCode.DUPLICATE_RISK_RESERVATION,
                reason=f"Risk reservation already exists for signal_id: '{trade_input.signal_id}'",
                signal_id=trade_input.signal_id,
                symbol=trade_input.symbol,
                side=trade_input.side,
                calculation_version=cfg.calculation_version,
                timestamp=eval_ts,
            )

    # Validate active reservations consistency
    port_res_risk = sum((r.monetary_risk for r in deduped_reservations), Decimal("0"))
    port_res_notional = sum((r.notional for r in deduped_reservations), Decimal("0"))

    # 1. Equity and Capital Sanity Gates & Authoritative Consistency Checks
    equity = portfolio_state.account_equity
    if not equity.is_finite() or equity <= Decimal("0"):
        return _invalid(
            RiskReasonCode.INVALID_EQUITY,
            f"portfolio_state.account_equity must be a positive finite Decimal: {equity}",
        )

    t_equity = trade_input.account_equity
    if not t_equity.is_finite() or t_equity <= Decimal("0"):
        return _invalid(
            RiskReasonCode.INVALID_EQUITY,
            f"trade_input.account_equity must be a positive finite Decimal: {t_equity}",
        )

    # Blocker 2: Explicit consistency check - fail closed on any mismatch
    if t_equity != equity:
        return _invalid(
            RiskReasonCode.INVALID_EQUITY,
            f"Account equity mismatch between trade_input ({t_equity}) "
            f"and portfolio_state ({equity})",
        )

    avail_cap = portfolio_state.available_capital
    if not avail_cap.is_finite() or avail_cap <= Decimal("0"):
        return _reject(
            RiskReasonCode.INSUFFICIENT_CAPITAL,
            f"portfolio_state.available_capital must be positive: {avail_cap}",
            equity=equity,
        )

    t_avail_cap = trade_input.available_capital
    if not t_avail_cap.is_finite() or t_avail_cap <= Decimal("0"):
        return _reject(
            RiskReasonCode.INSUFFICIENT_CAPITAL,
            f"trade_input.available_capital must be positive: {t_avail_cap}",
            equity=equity,
        )

    # Blocker 2: Explicit consistency check - fail closed on any capital mismatch
    if t_avail_cap != avail_cap:
        return _invalid(
            RiskReasonCode.INSUFFICIENT_CAPITAL,
            f"Available capital mismatch between trade_input ({t_avail_cap}) "
            f"and portfolio_state ({avail_cap})",
        )

    start_eq = portfolio_state.daily_starting_equity
    if not start_eq.is_finite() or start_eq <= Decimal("0"):
        return _invalid(
            RiskReasonCode.UNKNOWN_RISK_STATE,
            f"portfolio daily_starting_equity must be positive: {start_eq}",
        )

    # 2. Entry Price & Stop Price Validation
    entry = trade_input.entry_price
    if not entry.is_finite() or entry <= Decimal("0"):
        return _invalid(
            RiskReasonCode.INVALID_ENTRY_PRICE,
            f"entry_price must be a positive finite Decimal: {entry}",
        )

    stop = trade_input.stop_price
    if not stop.is_finite() or stop <= Decimal("0"):
        return _invalid(
            RiskReasonCode.INVALID_STOP_PRICE,
            f"stop_price must be a positive finite Decimal: {stop}",
        )

    # Stop Direction
    if trade_input.side == TradeSide.LONG and stop >= entry:
        return _reject(
            RiskReasonCode.INVALID_STOP_DIRECTION,
            f"LONG trade stop_price ({stop}) must be < entry_price ({entry})",
            equity=equity,
            entry=entry,
            stop=stop,
        )
    if trade_input.side == TradeSide.SHORT and stop <= entry:
        return _reject(
            RiskReasonCode.INVALID_STOP_DIRECTION,
            f"SHORT trade stop_price ({stop}) must be > entry_price ({entry})",
            equity=equity,
            entry=entry,
            stop=stop,
        )

    try:
        risk_dist = calculate_risk_distance(entry, stop)
    except RiskValidationError as err:
        return _reject(RiskReasonCode.INVALID_RISK_DISTANCE, str(err), equity=equity)

    stop_pct = calculate_stop_distance_pct(entry, stop)
    if stop_pct < cfg.minimum_stop_distance:
        return _reject(
            RiskReasonCode.STOP_DISTANCE_TOO_SMALL,
            f"Stop distance ratio {stop_pct:.4f} is below minimum {cfg.minimum_stop_distance}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
        )
    if stop_pct > cfg.maximum_stop_distance:
        return _reject(
            RiskReasonCode.STOP_DISTANCE_TOO_LARGE,
            f"Stop distance ratio {stop_pct:.4f} exceeds maximum {cfg.maximum_stop_distance}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
        )

    # 3. Contract Multiplier and Lot Size
    mult = trade_input.contract_multiplier
    if not mult.is_finite() or mult <= Decimal("0"):
        return _invalid(
            RiskReasonCode.INVALID_CONTRACT,
            f"contract_multiplier must be a positive finite Decimal: {mult}",
        )

    lot = trade_input.lot_size
    if lot <= 0:
        return _invalid(RiskReasonCode.INVALID_LOT_SIZE, f"lot_size must be positive: {lot}")

    # 4. Daily Loss Gate
    try:
        daily_loss, daily_loss_pct = calculate_daily_loss(start_eq, portfolio_state.current_equity)
    except RiskValidationError as err:
        return _invalid(RiskReasonCode.UNKNOWN_RISK_STATE, str(err))

    if daily_loss_pct >= cfg.daily_loss_hard_limit:
        return _reject(
            RiskReasonCode.DAILY_LOSS_LIMIT_REACHED,
            f"Daily loss {daily_loss_pct:.4f} reached or exceeded hard limit "
            f"{cfg.daily_loss_hard_limit}. All new trades halted.",
            equity=equity,
            loss=daily_loss,
        )

    # Dynamic trade risk budget with soft-limit throttling
    base_max_trade_risk = equity * cfg.max_risk_per_trade
    if daily_loss_pct >= cfg.daily_loss_soft_limit:
        # Throttling rule: risk halved and bounded by remaining buffer before hard limit
        remaining_budget = max(Decimal("0"), (start_eq * cfg.daily_loss_hard_limit) - daily_loss)
        effective_max_trade_risk = min(base_max_trade_risk * Decimal("0.50"), remaining_budget)
    else:
        effective_max_trade_risk = base_max_trade_risk

    # 5. Position Sizing
    risk_per_unit = calculate_risk_per_unit(risk_dist, mult)

    if trade_input.proposed_quantity is not None:
        qty = trade_input.proposed_quantity
        if qty <= 0:
            return _reject(
                RiskReasonCode.POSITION_SIZE_TOO_SMALL,
                f"proposed_quantity must be positive: {qty}",
                equity=equity,
                entry=entry,
                stop=stop,
                risk_dist=risk_dist,
            )
        if qty % lot != 0:
            return _reject(
                RiskReasonCode.INVALID_LOT_SIZE,
                f"proposed_quantity ({qty}) is not an exact multiple of lot_size ({lot})",
                equity=equity,
                entry=entry,
                stop=stop,
                risk_dist=risk_dist,
            )
        trade_risk = risk_per_unit * Decimal(qty)
        if trade_risk > effective_max_trade_risk:
            return _reject(
                RiskReasonCode.RISK_LIMIT_EXCEEDED,
                f"Trade risk {trade_risk} exceeds effective risk budget {effective_max_trade_risk}",
                equity=equity,
                entry=entry,
                stop=stop,
                risk_dist=risk_dist,
                risk_amt=trade_risk,
                qty=qty,
            )
    else:
        # Sizing determination
        raw_qty = effective_max_trade_risk / risk_per_unit
        dec_lot = Decimal(lot)
        if raw_qty < dec_lot:
            return _reject(
                RiskReasonCode.POSITION_SIZE_TOO_SMALL,
                f"Affordable quantity {raw_qty:.2f} is smaller than one lot size {lot}",
                equity=equity,
                entry=entry,
                stop=stop,
                risk_dist=risk_dist,
            )
        remainder = raw_qty % dec_lot
        if remainder != Decimal("0"):
            if not cfg.allow_lot_flooring:
                return _reject(
                    RiskReasonCode.INVALID_LOT_SIZE,
                    f"Affordable quantity {raw_qty} is not an exact multiple of lot_size {lot}. "
                    "Silent rounding/flooring is strictly forbidden under default policy.",
                    equity=equity,
                    entry=entry,
                    stop=stop,
                    risk_dist=risk_dist,
                )
            # Explicit flooring to whole lot multiples
            floored_lots = int(raw_qty // dec_lot)
            qty = floored_lots * lot
            trade_risk = risk_per_unit * Decimal(qty)
            if trade_risk > effective_max_trade_risk:
                return _reject(
                    RiskReasonCode.RISK_LIMIT_EXCEEDED,
                    f"Floored trade risk {trade_risk} exceeds effective risk budget "
                    f"{effective_max_trade_risk}",
                    equity=equity,
                    entry=entry,
                    stop=stop,
                    risk_dist=risk_dist,
                    risk_amt=trade_risk,
                    qty=qty,
                )
        else:
            qty = int(raw_qty)
            trade_risk = risk_per_unit * Decimal(qty)

    # 6. Single-Position Notional Limit
    trade_notional = calculate_notional(entry, qty, mult)
    max_pos_notional = equity * cfg.max_single_position_notional
    if trade_notional > max_pos_notional:
        return _reject(
            RiskReasonCode.POSITION_NOTIONAL_EXCEEDED,
            f"Trade notional {trade_notional} exceeds single-position limit {max_pos_notional}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
            risk_amt=trade_risk,
            qty=qty,
            notional=trade_notional,
        )

    # 7. Portfolio Risk Limit
    current_reserved_risk = max(portfolio_state.reserved_risk, port_res_risk)
    risk_before_pct = current_reserved_risk / equity
    risk_after = current_reserved_risk + trade_risk
    risk_after_pct = risk_after / equity

    if risk_after_pct > cfg.max_portfolio_risk:
        return _reject(
            RiskReasonCode.PORTFOLIO_RISK_EXCEEDED,
            f"Portfolio risk after trade {risk_after_pct:.4f} exceeds limit "
            f"{cfg.max_portfolio_risk:.4f}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
            risk_amt=trade_risk,
            qty=qty,
            notional=trade_notional,
            risk_before=risk_before_pct,
            risk_after=risk_after_pct,
        )

    # 8. Portfolio Notional Limit
    current_reserved_notional = max(portfolio_state.reserved_notional, port_res_notional)
    notional_after = current_reserved_notional + trade_notional
    notional_after_pct = notional_after / equity

    if notional_after_pct > cfg.max_portfolio_notional:
        return _reject(
            RiskReasonCode.PORTFOLIO_NOTIONAL_EXCEEDED,
            f"Portfolio notional after trade {notional_after_pct:.4f} exceeds limit "
            f"{cfg.max_portfolio_notional:.4f}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
            risk_amt=trade_risk,
            qty=qty,
            notional=trade_notional,
            risk_before=risk_before_pct,
            risk_after=risk_after_pct,
        )

    # 9. Max Open Trades Limit
    active_count = portfolio_state.open_trade_count + len(deduped_reservations) + 1
    if active_count > cfg.max_open_trades:
        return _reject(
            RiskReasonCode.MAX_OPEN_TRADES_EXCEEDED,
            f"Active trade count after trade ({active_count}) exceeds max_open_trades "
            f"({cfg.max_open_trades})",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
            risk_amt=trade_risk,
            qty=qty,
            notional=trade_notional,
            risk_before=risk_before_pct,
            risk_after=risk_after_pct,
        )

    # 10. Available Capital / Collateral Check
    req_capital = (
        trade_input.collateral_required
        if trade_input.collateral_required is not None
        else calculate_required_capital(trade_risk, cfg.risk_reserve_buffer)
    )
    if req_capital > avail_cap:
        return _reject(
            RiskReasonCode.INSUFFICIENT_CAPITAL,
            f"Required capital {req_capital} exceeds available capital {avail_cap}",
            equity=equity,
            entry=entry,
            stop=stop,
            risk_dist=risk_dist,
            risk_amt=trade_risk,
            qty=qty,
            notional=trade_notional,
            risk_before=risk_before_pct,
            risk_after=risk_after_pct,
        )

    # 11. Correlated Exposure Groups
    for group in cfg.correlated_groups:
        if trade_input.symbol in group.symbols:
            group_res_risk = sum(
                res.monetary_risk for res in deduped_reservations if res.symbol in group.symbols
            )
            non_group_res_risk = sum(
                res.monetary_risk for res in deduped_reservations if res.symbol not in group.symbols
            )
            unaccounted_reserved_risk = max(
                Decimal("0"),
                portfolio_state.reserved_risk - (group_res_risk + non_group_res_risk),
            )
            group_risk_before = group_res_risk + unaccounted_reserved_risk
            grp_risk_after = group_risk_before + trade_risk
            grp_risk_after_pct = grp_risk_after / equity
            if grp_risk_after_pct > group.max_group_risk:
                return _reject(
                    RiskReasonCode.CORRELATED_RISK_EXCEEDED,
                    f"Correlated group '{group.group_id}' risk after trade "
                    f"({grp_risk_after_pct:.4f}) exceeds limit ({group.max_group_risk:.4f})",
                    equity=equity,
                    entry=entry,
                    stop=stop,
                    risk_dist=risk_dist,
                    risk_amt=trade_risk,
                    qty=qty,
                    notional=trade_notional,
                    risk_before=risk_before_pct,
                    risk_after=risk_after_pct,
                )

    # 12. Trade Approved
    return RiskDecision(
        decision=RiskDecisionState.APPROVED,
        reason_code=RiskReasonCode.APPROVED,
        reason="Trade risk approved within all portfolio constraints",
        signal_id=trade_input.signal_id,
        symbol=trade_input.symbol,
        side=trade_input.side,
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        risk_distance=risk_dist,
        risk_amount=trade_risk,
        quantity=qty,
        notional=trade_notional,
        portfolio_risk_before=risk_before_pct,
        portfolio_risk_after=risk_after_pct,
        daily_loss=daily_loss,
        calculation_version=cfg.calculation_version,
        timestamp=eval_ts,
    )


def _notify_risk_observability(decision: RiskDecision, trade_input: RiskInput) -> None:
    """Passive risk observability notification; never modifies decision or raises exceptions."""
    from alphaforge.observability.hub import observe_risk_evaluation

    observe_risk_evaluation(decision, trade_input)


def evaluate_trade_risk(
    trade_input: RiskInput,
    portfolio_state: PortfolioRiskState,
    config: RiskConfig | None = None,
) -> RiskDecision:
    """
    Pure deterministic pre-trade risk validation gate with passive observability notification.
    """
    decision = _evaluate_trade_risk_internal(trade_input, portfolio_state, config)
    _notify_risk_observability(decision, trade_input)
    return decision


class RiskEngine:
    """
    Concurrency-safe Risk Engine with in-memory atomic risk reservation and idempotency.
    Guarantees atomic updates: concurrent approval attempts never breach portfolio risk,
    notional, or open trade constraints.

    Single Source of Truth (SSOT):
    In Phase 5 V1, RiskEngine._reservations is the authoritative Single Source of Truth
    for active risk reservations. External snapshots passed via PortfolioRiskState are
    reconciled and deduplicated by stable reservation_id and signal_id. Inconsistent
    or conflicting reservation states fail closed immediately.

    Restart & Persistence Semantics:
    In Phase 5 V1, all reservations in RiskEngine are volatile and held strictly in memory.
    State persistence, crash recovery, and restart reconciliation are explicitly out of scope
    for Phase 5 and belong to Phase 8. On process termination, active reservations are lost.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self._config = config or RiskConfig()
        self._lock = threading.Lock()
        self._reservations: dict[str, RiskReservation] = {}

    @property
    def config(self) -> RiskConfig:
        """Return configured risk parameters."""
        return self._config

    def request_risk_reservation(
        self,
        trade_input: RiskInput,
        portfolio_state: PortfolioRiskState,
        config: RiskConfig | None = None,
    ) -> tuple[RiskDecision, RiskReservation | None]:
        """
        Atomically evaluate trade risk and, if approved, grant an immutable RiskReservation.
        Idempotent: Re-evaluation of an already reserved signal_id or reservation_id fails
        closed with DUPLICATE_RISK_RESERVATION.
        """
        cfg = config or self._config
        expected_res_id = f"RES-{trade_input.signal_id}-{trade_input.symbol}"
        eval_ts = trade_input.evaluation_timestamp

        with self._lock:
            # 1. Authoritative internal idempotency check: Same signal_id cannot reserve twice
            if trade_input.signal_id in self._reservations:
                existing_res = self._reservations[trade_input.signal_id]
                decision = RiskDecision(
                    decision=RiskDecisionState.REJECTED,
                    reason_code=RiskReasonCode.DUPLICATE_RISK_RESERVATION,
                    reason=(
                        f"Risk reservation already exists for signal_id: '{trade_input.signal_id}' "
                        f"(reservation_id: '{existing_res.reservation_id}')"
                    ),
                    signal_id=trade_input.signal_id,
                    symbol=trade_input.symbol,
                    side=trade_input.side,
                    calculation_version=cfg.calculation_version,
                    timestamp=eval_ts,
                )
                return decision, None

            for res in self._reservations.values():
                if res.reservation_id == expected_res_id:
                    decision = RiskDecision(
                        decision=RiskDecisionState.REJECTED,
                        reason_code=RiskReasonCode.DUPLICATE_RISK_RESERVATION,
                        reason=(
                            f"Risk reservation already exists for "
                            f"reservation_id: '{expected_res_id}'"
                        ),
                        signal_id=trade_input.signal_id,
                        symbol=trade_input.symbol,
                        side=trade_input.side,
                        calculation_version=cfg.calculation_version,
                        timestamp=eval_ts,
                    )
                    return decision, None

            # 2. Reconcile reservations across authoritative engine state and portfolio snapshot
            all_reservations = list(self._reservations.values()) + list(
                portfolio_state.active_reservations
            )
            try:
                canonical_reservations = reconcile_and_deduplicate_reservations(all_reservations)
            except RiskValidationError as err:
                decision = RiskDecision(
                    decision=RiskDecisionState.INVALID,
                    reason_code=RiskReasonCode.UNKNOWN_RISK_STATE,
                    reason=f"Inconsistent reservation state during reconciliation: {err}",
                    signal_id=trade_input.signal_id,
                    symbol=trade_input.symbol,
                    side=trade_input.side,
                    calculation_version=cfg.calculation_version,
                    timestamp=eval_ts,
                )
                return decision, None

            # Check if trade_input identity already exists in canonical_reservations
            for r in canonical_reservations:
                if r.signal_id == trade_input.signal_id or r.reservation_id == expected_res_id:
                    decision = RiskDecision(
                        decision=RiskDecisionState.REJECTED,
                        reason_code=RiskReasonCode.DUPLICATE_RISK_RESERVATION,
                        reason=(
                            f"Risk reservation already exists for signal '{trade_input.signal_id}' "
                            f"(reservation_id: '{r.reservation_id}')"
                        ),
                        signal_id=trade_input.signal_id,
                        symbol=trade_input.symbol,
                        side=trade_input.side,
                        calculation_version=cfg.calculation_version,
                        timestamp=eval_ts,
                    )
                    return decision, None

            # 3. Deterministic mathematical aggregation to prevent double counting
            try:
                port_deduped = reconcile_and_deduplicate_reservations(
                    portfolio_state.active_reservations
                )
            except RiskValidationError as err:
                decision = RiskDecision(
                    decision=RiskDecisionState.INVALID,
                    reason_code=RiskReasonCode.UNKNOWN_RISK_STATE,
                    reason=f"Inconsistent active_reservations in portfolio_state: {err}",
                    signal_id=trade_input.signal_id,
                    symbol=trade_input.symbol,
                    side=trade_input.side,
                    calculation_version=cfg.calculation_version,
                    timestamp=eval_ts,
                )
                return decision, None

            port_res_risk = sum((r.monetary_risk for r in port_deduped), Decimal("0"))
            port_res_notional = sum((r.notional for r in port_deduped), Decimal("0"))

            if portfolio_state.reserved_risk < port_res_risk:
                decision = RiskDecision(
                    decision=RiskDecisionState.INVALID,
                    reason_code=RiskReasonCode.UNKNOWN_RISK_STATE,
                    reason=(
                        f"portfolio_state.reserved_risk ({portfolio_state.reserved_risk}) "
                        f"is less than sum of active reservations risk ({port_res_risk})"
                    ),
                    signal_id=trade_input.signal_id,
                    symbol=trade_input.symbol,
                    side=trade_input.side,
                    calculation_version=cfg.calculation_version,
                    timestamp=eval_ts,
                )
                return decision, None

            if portfolio_state.reserved_notional < port_res_notional:
                decision = RiskDecision(
                    decision=RiskDecisionState.INVALID,
                    reason_code=RiskReasonCode.UNKNOWN_RISK_STATE,
                    reason=(
                        f"portfolio_state.reserved_notional ({portfolio_state.reserved_notional}) "
                        f"is less than sum of active reservations notional ({port_res_notional})"
                    ),
                    signal_id=trade_input.signal_id,
                    symbol=trade_input.symbol,
                    side=trade_input.side,
                    calculation_version=cfg.calculation_version,
                    timestamp=eval_ts,
                )
                return decision, None

            base_reserved_risk = portfolio_state.reserved_risk - port_res_risk
            base_reserved_notional = portfolio_state.reserved_notional - port_res_notional

            canonical_res_risk = sum(
                (r.monetary_risk for r in canonical_reservations), Decimal("0")
            )
            canonical_res_notional = sum((r.notional for r in canonical_reservations), Decimal("0"))

            effective_reserved_risk = base_reserved_risk + canonical_res_risk
            effective_reserved_notional = base_reserved_notional + canonical_res_notional

            effective_portfolio_state = PortfolioRiskState(
                account_equity=portfolio_state.account_equity,
                available_capital=portfolio_state.available_capital,
                open_trade_count=portfolio_state.open_trade_count,
                reserved_risk=effective_reserved_risk,
                reserved_notional=effective_reserved_notional,
                daily_starting_equity=portfolio_state.daily_starting_equity,
                current_equity=portfolio_state.current_equity,
                active_reservations=canonical_reservations,
            )

            decision = evaluate_trade_risk(trade_input, effective_portfolio_state, config=cfg)

            if decision.decision != RiskDecisionState.APPROVED:
                return decision, None

            # Create immutable RiskReservation
            assert decision.quantity is not None
            assert decision.risk_amount is not None
            assert decision.notional is not None

            reservation = RiskReservation(
                reservation_id=expected_res_id,
                signal_id=trade_input.signal_id,
                symbol=trade_input.symbol,
                side=trade_input.side,
                entry_price=trade_input.entry_price,
                stop_price=trade_input.stop_price,
                quantity=decision.quantity,
                monetary_risk=decision.risk_amount,
                notional=decision.notional,
                created_timestamp=trade_input.evaluation_timestamp,
                calculation_version=cfg.calculation_version,
            )

            self._reservations[trade_input.signal_id] = reservation
            return decision, reservation

    def get_reservation(self, identifier: str) -> RiskReservation | None:
        """
        Atomically retrieve an active reservation by signal_id or reservation_id.
        Returns None if no matching reservation is found.
        """
        with self._lock:
            if identifier in self._reservations:
                return self._reservations[identifier]
            for res in self._reservations.values():
                if res.reservation_id == identifier:
                    return res
            return None

    def release_reservation(self, identifier: str) -> bool:
        """Atomically release an active risk reservation by signal_id or reservation_id."""
        with self._lock:
            if identifier in self._reservations:
                del self._reservations[identifier]
                return True
            for sig_id, res in list(self._reservations.items()):
                if res.reservation_id == identifier:
                    del self._reservations[sig_id]
                    return True
            return False

    def get_active_reservations(self) -> tuple[RiskReservation, ...]:
        """Thread-safe snapshot of all currently active reservations."""
        with self._lock:
            return tuple(self._reservations.values())

    @property
    def total_reserved_risk(self) -> Decimal:
        """Thread-safe calculation of total monetary risk across active reservations."""
        with self._lock:
            return sum((r.monetary_risk for r in self._reservations.values()), Decimal("0"))

    @property
    def total_reserved_notional(self) -> Decimal:
        """Thread-safe calculation of total notional exposure across active reservations."""
        with self._lock:
            return sum((r.notional for r in self._reservations.values()), Decimal("0"))

    def clear(self) -> None:
        """Reset all active reservations (for test isolation)."""
        with self._lock:
            self._reservations.clear()
