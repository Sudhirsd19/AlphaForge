"""
AlphaForge Shadow Mode Observation & Forward Comparator.

Passively observes market data events, evaluates frozen strategy and risk decisions,
simulates hypothetical execution, and records diagnostic comparison records against
subsequent market outcomes with ZERO broker order submissions.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import TYPE_CHECKING

from alphaforge.execution.enums import OrderSide
from alphaforge.paper_shadow.enums import ShadowComparisonOutcome
from alphaforge.paper_shadow.fill_simulator import DeterministicFillSimulator
from alphaforge.paper_shadow.models import (
    PaperShadowConfig,
    ShadowObservationRecord,
)
from alphaforge.risk.enums import RiskDecisionState, TradeSide

if TYPE_CHECKING:
    from alphaforge.core.models import StrategySignal
    from alphaforge.data.models import MarketCandle
    from alphaforge.risk.models import RiskDecision


class ShadowComparator:
    """
    Diagnostic shadow mode evaluator and outcome comparator.
    Guarantees 100% passive non-trading observation without external side effects.
    """

    def __init__(
        self,
        config: PaperShadowConfig,
        fill_simulator: DeterministicFillSimulator | None = None,
    ) -> None:
        self._config = config
        self._fill_sim = fill_simulator or DeterministicFillSimulator(
            cost_config=config.cost_config,
            ohlc_policy=config.ohlc_policy,
            contract_multiplier=config.contract_multiplier,
        )
        self._lock = threading.RLock()
        self._observations: list[ShadowObservationRecord] = []
        self._obs_counter: int = 0
        self._hypothetical_position_qty: int = 0
        self._hypothetical_entry_price: Decimal | None = None
        self._hypothetical_side: TradeSide | None = None
        self._hypothetical_realized_pnl = Decimal("0")

    def record_observation(
        self,
        market_event_id: str,
        symbol: str,
        candle: MarketCandle,
        signal: StrategySignal | None,
        risk_decision: RiskDecision | None,
        evaluation_latency_ns: int = 0,
        subsequent_candle: MarketCandle | None = None,
        notes: str = "",
    ) -> ShadowObservationRecord:
        """
        Record a comprehensive shadow mode observation.
        Simulates hypothetical execution and compares against forward market prices.
        """
        with self._lock:
            self._obs_counter += 1
            obs_id = f"SHADOW-OBS-{self._obs_counter:06d}"
            now = candle.exchange_timestamp

            signal_dec = signal.decision.value if signal is not None else "NO_SIGNAL"
            signal_dir = signal.direction.value if signal is not None else "FLAT"
            signal_id = signal.signal_id if signal is not None else None

            risk_dec = risk_decision.decision.value if risk_decision is not None else None
            risk_reason = risk_decision.reason_code.value if risk_decision is not None else None

            intended_order_id: str | None = None
            intended_qty: int | None = None
            hypo_fill_price: Decimal | None = None
            hypo_fill_qty: int | None = None
            outcome = ShadowComparisonOutcome.NO_SIGNAL

            if signal is not None and signal_dec == "ACCEPT":
                if (
                    risk_decision is not None
                    and risk_decision.decision == RiskDecisionState.APPROVED
                ):
                    intended_order_id = f"HYPO-ORD-{obs_id}"
                    intended_qty = risk_decision.quantity or self._config.lot_size
                    trade_side = TradeSide.LONG if signal_dir == "LONG" else TradeSide.SHORT

                    # Simulate hypothetical fill
                    sim_fill = self._fill_sim.simulate_market_order(
                        order_id=intended_order_id,
                        symbol=symbol,
                        side=OrderSide.BUY if trade_side == TradeSide.LONG else OrderSide.SELL,
                        quantity=intended_qty,
                        candle=candle,
                        is_entry=True,
                    )
                    hypo_fill_price = sim_fill.effective_price
                    hypo_fill_qty = sim_fill.filled_qty

                    # Track hypothetical position
                    self._hypothetical_position_qty = hypo_fill_qty
                    self._hypothetical_entry_price = hypo_fill_price
                    self._hypothetical_side = trade_side

                    outcome = ShadowComparisonOutcome.MATCH_SIMULATED
                else:
                    outcome = ShadowComparisonOutcome.REJECTED_BY_RISK

            # Calculate hypothetical unrealized P&L
            hypo_unrealized = Decimal("0")
            if (
                self._hypothetical_position_qty > 0
                and self._hypothetical_entry_price is not None
                and self._hypothetical_side is not None
            ):
                ref_close = candle.close
                if self._hypothetical_side == TradeSide.LONG:
                    p_diff = ref_close - self._hypothetical_entry_price
                else:
                    p_diff = self._hypothetical_entry_price - ref_close
                hypo_unrealized = (
                    p_diff
                    * Decimal(self._hypothetical_position_qty)
                    * self._config.contract_multiplier
                )

            subsequent_price = subsequent_candle.close if subsequent_candle is not None else None

            obs = ShadowObservationRecord(
                observation_id=obs_id,
                timestamp=now,
                symbol=symbol,
                market_event_id=market_event_id,
                signal_decision=signal_dec,
                signal_direction=signal_dir,
                signal_id=signal_id,
                risk_decision=risk_dec,
                risk_reason_code=risk_reason,
                intended_client_order_id=intended_order_id,
                intended_quantity=intended_qty,
                hypothetical_fill_price=hypo_fill_price,
                hypothetical_fill_qty=hypo_fill_qty,
                hypothetical_position_qty=self._hypothetical_position_qty,
                hypothetical_realized_pnl=self._hypothetical_realized_pnl,
                hypothetical_unrealized_pnl=hypo_unrealized,
                evaluation_latency_ns=evaluation_latency_ns,
                market_price_reference=candle.close,
                subsequent_price_check=subsequent_price,
                comparison_outcome=outcome,
                notes=notes,
            )

            self._observations.append(obs)
            return obs

    def get_observations(self) -> tuple[ShadowObservationRecord, ...]:
        """Return defensive snapshot of shadow observations."""
        with self._lock:
            return tuple(self._observations)

    def reset(self) -> None:
        """Reset shadow observation state."""
        with self._lock:
            self._observations.clear()
            self._obs_counter = 0
            self._hypothetical_position_qty = 0
            self._hypothetical_entry_price = None
            self._hypothetical_side = None
            self._hypothetical_realized_pnl = Decimal("0")
