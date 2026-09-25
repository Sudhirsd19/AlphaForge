"""
AlphaForge Fixed-Point P&L and Performance Tracker.

Maintains thread-safe accounting for realized P&L, mark-to-market unrealized P&L,
transaction friction, slippage accrual, drawdown, win rate, and profit factor.
Strictly enforces Phase 6 mathematical formulas and fixed-point Decimal arithmetic.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import TYPE_CHECKING

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.cost.calculator import (
    calculate_gross_pnl,
    calculate_net_pnl,
)
from alphaforge.cost.models import CostConfig
from alphaforge.paper_shadow.models import (
    PaperPerformanceMetrics,
    PaperTradeRecord,
)
from alphaforge.risk.enums import TradeSide
from alphaforge.risk.models import PortfolioRiskState

if TYPE_CHECKING:
    from datetime import datetime

    from alphaforge.backtest.fills import SimulatedFill
    from alphaforge.data.models import MarketCandle


class ActivePositionState:
    """Internal mutable position state protected by PnLTracker lock."""

    def __init__(
        self,
        symbol: str,
        side: TradeSide,
        quantity: int,
        entry_price: Decimal,
        entry_order_id: str,
        entry_timestamp: datetime,
        multiplier: Decimal = Decimal("1"),
        stop_price: Decimal | None = None,
        target_price: Decimal | None = None,
        strategy_id: str = "",
        strategy_version: str = "",
        signal_id: str = "",
        initial_stop_price: Decimal | None = None,
    ) -> None:
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.entry_order_id = entry_order_id
        self.entry_timestamp = entry_timestamp
        self.multiplier = multiplier
        self.stop_price = stop_price
        self.target_price = target_price
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.signal_id = signal_id
        self.initial_stop_price = initial_stop_price if initial_stop_price is not None else stop_price
        self.is_breakeven_trailed: bool = False
        self.accumulated_entry_fee = Decimal("0")
        self.accumulated_entry_slippage = Decimal("0")


class PaperPnLTracker:
    """
    Authoritative P&L, friction, and portfolio exposure tracker for Paper Trading.
    All calculations are deterministic, thread-safe, and use fixed-point Decimal math.
    """

    def __init__(
        self,
        cost_config: CostConfig | None = None,
        contract_multiplier: Decimal = Decimal("1"),
        initial_capital: Decimal = Decimal("1000000"),
    ) -> None:
        self._cost_config = cost_config or CostConfig()
        self._multiplier = contract_multiplier
        self._initial_capital = initial_capital
        self._lock = threading.RLock()

        self._active_positions: dict[str, ActivePositionState] = {}
        self._closed_trades: list[PaperTradeRecord] = []
        self._realized_pnl = Decimal("0")
        self._total_fees = Decimal("0")
        self._total_slippage = Decimal("0")
        self._peak_equity = initial_capital
        self._max_drawdown = Decimal("0")
        self._max_drawdown_pct = Decimal("0")
        self._trade_counter: int = 0

    @property
    def cost_config(self) -> CostConfig:
        return self._cost_config

    @property
    def multiplier(self) -> Decimal:
        return self._multiplier

    def record_entry_fill(
        self,
        fill: SimulatedFill,
        stop_price: Decimal | None = None,
        target_price: Decimal | None = None,
        strategy_id: str = "",
        strategy_version: str = "",
        signal_id: str = "",
    ) -> None:
        """Record an entry fill and update active position with authoritative exit levels."""
        with self._lock:
            sym = fill.symbol
            existing = self._active_positions.get(sym)

            if existing is None:
                pos = ActivePositionState(
                    symbol=sym,
                    side=fill.side,
                    quantity=fill.filled_qty,
                    entry_price=fill.effective_price,
                    entry_order_id=fill.order_id,
                    entry_timestamp=fill.timestamp,
                    multiplier=self._multiplier,
                    stop_price=stop_price,
                    target_price=target_price,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                    signal_id=signal_id,
                    initial_stop_price=stop_price,
                )
                pos.accumulated_entry_fee += fill.fee
                pos.accumulated_entry_slippage += fill.slippage_loss
                self._active_positions[sym] = pos
            else:
                # Scale-in or partial fill continuation
                if existing.side != fill.side:
                    raise DataIntegrityError(
                        f"Cannot record entry fill for '{sym}' with conflicting side: "
                        f"Existing {existing.side} != New {fill.side}"
                    )
                prev_notional = existing.entry_price * Decimal(existing.quantity)
                new_notional = fill.effective_price * Decimal(fill.filled_qty)
                total_qty = existing.quantity + fill.filled_qty
                avg_price = (prev_notional + new_notional) / Decimal(total_qty)

                existing.quantity = total_qty
                existing.entry_price = avg_price
                if stop_price is not None:
                    existing.stop_price = stop_price
                if target_price is not None:
                    existing.target_price = target_price
                existing.accumulated_entry_fee += fill.fee
                existing.accumulated_entry_slippage += fill.slippage_loss

            self._total_fees += fill.fee
            self._total_slippage += fill.slippage_loss

    def record_exit_fill(self, fill: SimulatedFill, exit_reason: str = "EXIT") -> PaperTradeRecord:
        """Record an exit fill, close active position (or partial exit), and calculate net P&L."""
        with self._lock:
            sym = fill.symbol
            pos = self._active_positions.get(sym)
            if pos is None:
                raise DataIntegrityError(f"Cannot record exit fill: No active position for '{sym}'")

            if fill.filled_qty > pos.quantity:
                raise DataIntegrityError(
                    f"Exit quantity {fill.filled_qty} exceeds position quantity {pos.quantity}"
                )

            # Calculate gross and net P&L using Phase 6 formulas
            gross_pnl = calculate_gross_pnl(
                effective_entry_price=pos.entry_price,
                effective_exit_price=fill.effective_price,
                quantity=fill.filled_qty,
                contract_multiplier=self._multiplier,
                side=pos.side,
            )

            # Pro-rated entry fee + exit fee + fixed cost
            entry_fee_portion = (
                pos.accumulated_entry_fee * Decimal(fill.filled_qty) / Decimal(pos.quantity)
            )
            total_trade_fee = entry_fee_portion + fill.fee
            net_pnl = calculate_net_pnl(gross_pnl=gross_pnl, transaction_cost=total_trade_fee)

            entry_slippage_portion = (
                pos.accumulated_entry_slippage * Decimal(fill.filled_qty) / Decimal(pos.quantity)
            )
            total_trade_slippage = entry_slippage_portion + fill.slippage_loss

            self._trade_counter += 1
            trade_id = f"TRD-{sym}-{self._trade_counter:06d}"

            trade = PaperTradeRecord(
                trade_id=trade_id,
                symbol=sym,
                side=pos.side,
                quantity=fill.filled_qty,
                entry_order_id=pos.entry_order_id,
                exit_order_id=fill.order_id,
                entry_timestamp=pos.entry_timestamp,
                exit_timestamp=fill.timestamp,
                entry_price=pos.entry_price,
                exit_price=fill.effective_price,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                fees=total_trade_fee,
                slippage_loss=total_trade_slippage,
                is_closed=True,
                exit_reason=exit_reason,
            )

            self._closed_trades.append(trade)
            self._realized_pnl += net_pnl
            self._total_fees += fill.fee
            self._total_slippage += fill.slippage_loss

            # Update remaining active position
            if fill.filled_qty == pos.quantity:
                self._active_positions.pop(sym)
            else:
                pos.quantity -= fill.filled_qty
                pos.accumulated_entry_fee -= entry_fee_portion
                pos.accumulated_entry_slippage -= entry_slippage_portion

            # Update peak equity and drawdown
            current_equity = self._initial_capital + self._realized_pnl
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity

            drawdown = self._peak_equity - current_equity
            if drawdown > self._max_drawdown:
                self._max_drawdown = drawdown
                if self._peak_equity > Decimal("0"):
                    self._max_drawdown_pct = (drawdown / self._peak_equity) * Decimal("100")

            return trade

    def compute_unrealized_pnl(self, current_candles: dict[str, MarketCandle]) -> Decimal:
        """Compute mark-to-market unrealized P&L across all active positions."""
        with self._lock:
            total_unrealized = Decimal("0")
            for sym, pos in self._active_positions.items():
                candle = current_candles.get(sym)
                if candle is None:
                    continue
                mark_price = candle.close
                gross = calculate_gross_pnl(
                    effective_entry_price=pos.entry_price,
                    effective_exit_price=mark_price,
                    quantity=pos.quantity,
                    contract_multiplier=self._multiplier,
                    side=pos.side,
                )
                total_unrealized += gross
            return total_unrealized

    def get_metrics(
        self, current_candles: dict[str, MarketCandle] | None = None
    ) -> PaperPerformanceMetrics:
        """Generate immutable quantitative performance metrics."""
        with self._lock:
            trades = self._closed_trades
            trade_count = len(trades)
            winning_trades = sum(1 for t in trades if t.net_pnl > Decimal("0"))
            losing_trades = sum(1 for t in trades if t.net_pnl < Decimal("0"))
            win_rate = (
                (Decimal(winning_trades) / Decimal(trade_count))
                if trade_count > 0
                else Decimal("0")
            )

            gross_pnl = sum((t.gross_pnl for t in trades), Decimal("0"))
            net_pnl = sum((t.net_pnl for t in trades), Decimal("0"))
            avg_trade = (net_pnl / Decimal(trade_count)) if trade_count > 0 else Decimal("0")

            gross_wins = sum(
                (t.gross_pnl for t in trades if t.gross_pnl > Decimal("0")), Decimal("0")
            )
            gross_losses = abs(
                sum((t.gross_pnl for t in trades if t.gross_pnl < Decimal("0")), Decimal("0"))
            )
            profit_factor = (
                (gross_wins / gross_losses) if gross_losses > Decimal("0") else Decimal("0")
            )

            unrealized = (
                self.compute_unrealized_pnl(current_candles)
                if current_candles is not None
                else Decimal("0")
            )
            exposure = sum(
                (
                    pos.entry_price * Decimal(pos.quantity) * self._multiplier
                    for pos in self._active_positions.values()
                ),
                Decimal("0"),
            )

            return PaperPerformanceMetrics(
                trade_count=trade_count,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
                win_rate=win_rate,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                total_fees=self._total_fees,
                total_slippage=self._total_slippage,
                max_drawdown=self._max_drawdown,
                max_drawdown_pct=self._max_drawdown_pct,
                average_trade_pnl=avg_trade,
                profit_factor=profit_factor,
                current_exposure=exposure,
                open_positions_count=len(self._active_positions),
                realized_pnl=self._realized_pnl,
                unrealized_pnl=unrealized,
            )

    def get_closed_trades(self) -> tuple[PaperTradeRecord, ...]:
        """Return defensive snapshot of closed trades."""
        with self._lock:
            return tuple(self._closed_trades)

    def get_active_positions(self) -> dict[str, ActivePositionState]:
        """Return shallow copy of active positions."""
        with self._lock:
            return dict(self._active_positions)

    def update_trailing_stop_to_breakeven(
        self,
        symbol: str,
        candle: MarketCandle,
    ) -> tuple[bool, Decimal | None, Decimal | None]:
        """
        Check if active position achieved +1R profit during the candle.
        If yes, trail stop_price to entry_price (Breakeven).
        Returns (trailed, old_stop, new_stop).
        """
        with self._lock:
            pos = self._active_positions.get(symbol)
            if pos is None or pos.is_breakeven_trailed:
                return False, None, None
            if pos.initial_stop_price is None or pos.stop_price is None:
                return False, None, None

            old_stop = pos.stop_price

            if pos.side == TradeSide.LONG:
                risk_dist = pos.entry_price - pos.initial_stop_price
                if risk_dist <= Decimal("0"):
                    return False, None, None
                # If candle high reached entry + 1R
                if candle.high >= (pos.entry_price + risk_dist):
                    if pos.stop_price < pos.entry_price:
                        pos.stop_price = pos.entry_price
                        pos.is_breakeven_trailed = True
                        return True, old_stop, pos.stop_price

            elif pos.side == TradeSide.SHORT:
                risk_dist = pos.initial_stop_price - pos.entry_price
                if risk_dist <= Decimal("0"):
                    return False, None, None
                # If candle low reached entry - 1R
                if candle.low <= (pos.entry_price - risk_dist):
                    if pos.stop_price > pos.entry_price:
                        pos.stop_price = pos.entry_price
                        pos.is_breakeven_trailed = True
                        return True, old_stop, pos.stop_price

            return False, None, None

    def get_portfolio_risk_state(
        self, current_candles: dict[str, MarketCandle] | None = None
    ) -> PortfolioRiskState:
        """
        Generate dynamic, authoritative PortfolioRiskState from evolving paper/shadow state.
        Never returns a static or hardcoded constant equity.
        """
        with self._lock:
            unrealized = (
                self.compute_unrealized_pnl(current_candles)
                if current_candles is not None
                else Decimal("0")
            )
            current_equity = self._initial_capital + self._realized_pnl + unrealized
            notional_allocated = sum(
                (
                    pos.entry_price * Decimal(pos.quantity) * pos.multiplier
                    for pos in self._active_positions.values()
                ),
                Decimal("0"),
            )
            available_capital = max(Decimal("0"), current_equity - notional_allocated)
            return PortfolioRiskState(
                account_equity=current_equity,
                available_capital=available_capital,
                daily_starting_equity=self._initial_capital,
                current_equity=current_equity,
            )

    def reset(self) -> None:
        """Reset P&L tracker state."""
        with self._lock:
            self._active_positions.clear()
            self._closed_trades.clear()
            self._realized_pnl = Decimal("0")
            self._total_fees = Decimal("0")
            self._total_slippage = Decimal("0")
            self._peak_equity = self._initial_capital
            self._max_drawdown = Decimal("0")
            self._max_drawdown_pct = Decimal("0")
            self._trade_counter = 0
