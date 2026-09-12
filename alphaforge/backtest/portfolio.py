"""
AlphaForge Portfolio & Derivatives Accounting Engine.
Implements deterministic Decimal portfolio accounting, single-position invariants,
strict separation of collateral, margin, realized/unrealized PnL, fees, and exposure,
and continuous MFE/MAE tracking.
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphaforge.backtest.models import BacktestTrade, EquitySnapshot
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


class PortfolioTracker:
    """
    Stateful deterministic portfolio ledger tracking collateral, margin,
    drawdown, excursions, and open position lifecycle.
    """

    def __init__(
        self,
        initial_capital: Decimal,
        base_currency: str = "INR",
        margin_rate: Decimal = Decimal("0"),
    ) -> None:
        if not isinstance(initial_capital, Decimal) or not initial_capital.is_finite():
            raise BacktestValidationError(
                f"initial_capital must be finite Decimal: {initial_capital}"
            )
        if initial_capital <= Decimal("0"):
            raise BacktestValidationError(
                f"initial_capital must be strictly positive: {initial_capital}"
            )

        self.initial_capital: Decimal = initial_capital
        self.base_currency: str = base_currency
        self.margin_rate: Decimal = margin_rate

        # Collateral and PnL states
        self.cash: Decimal = initial_capital
        self.margin_used: Decimal = Decimal("0")
        self.cumulative_realized_pnl: Decimal = Decimal("0")
        self.unrealized_pnl: Decimal = Decimal("0")
        self.cumulative_fees: Decimal = Decimal("0")
        self.cumulative_slippage: Decimal = Decimal("0")
        self.cumulative_other_costs: Decimal = Decimal("0")
        self.notional_exposure: Decimal = Decimal("0")

        # Peak and drawdown tracking
        self.peak_equity: Decimal = initial_capital
        self.max_drawdown: Decimal = Decimal("0")
        self.max_drawdown_pct: Decimal = Decimal("0")
        self.max_drawdown_duration_seconds: int = 0
        self._drawdown_start_ts: datetime | None = None

        # Single-position state
        self.has_open_position: bool = False
        self.position_symbol: str | None = None
        self.position_side: TradeSide | None = None
        self.position_quantity: int = 0
        self.entry_reference_price: Decimal = Decimal("0")
        self.entry_effective_price: Decimal = Decimal("0")
        self.entry_timestamp: datetime | None = None
        self.entry_signal_id: str | None = None
        self.strategy_version: str | None = None
        self.entry_fee: Decimal = Decimal("0")
        self.entry_slippage: Decimal = Decimal("0")

        # Excursions
        self.mfe: Decimal = Decimal("0")  # Max Favorable Excursion in points
        self.mae: Decimal = Decimal("0")  # Max Adverse Excursion in points

        # Trade history and snapshots
        self.completed_trades: list[BacktestTrade] = []
        self.equity_snapshots: list[EquitySnapshot] = []

    @property
    def equity(self) -> Decimal:
        """
        Total portfolio equity =
        starting_capital + realized_pnl + unrealized_pnl - fees - slippage - other_costs.
        Equivalently: Cash + Margin_Used + Unrealized_PnL.
        """
        return (
            self.initial_capital
            + self.cumulative_realized_pnl
            + self.unrealized_pnl
            - self.cumulative_fees
            - self.cumulative_slippage
            - self.cumulative_other_costs
        )

    def open_position(
        self,
        symbol: str,
        side: TradeSide,
        quantity: int,
        reference_price: Decimal,
        effective_price: Decimal,
        timestamp: datetime,
        signal_id: str,
        strategy_version: str,
        fee: Decimal,
        slippage_loss: Decimal,
        multiplier: Decimal = Decimal("1"),
    ) -> None:
        """
        Open a new position.
        Enforces single-entry model: raises BacktestValidationError if a position is already open.
        """
        if self.has_open_position:
            pos_desc = f"{self.position_side} {self.position_quantity} {self.position_symbol}"
            raise BacktestValidationError(
                f"Single-entry model violation: Cannot open {side} {quantity} {symbol} "
                f"while existing position {pos_desc} is open"
            )
        if quantity <= 0:
            raise BacktestValidationError(f"Entry quantity must be > 0: {quantity}")
        if reference_price <= Decimal("0") or effective_price <= Decimal("0"):
            raise BacktestValidationError("Entry prices must be strictly positive")
        if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
            raise BacktestValidationError(f"Entry timestamp must be UTC: {timestamp}")

        self.has_open_position = True
        self.position_symbol = symbol
        self.position_side = side
        self.position_quantity = quantity
        self.entry_reference_price = reference_price
        self.entry_effective_price = effective_price
        self.entry_timestamp = timestamp
        self.entry_signal_id = signal_id
        self.strategy_version = strategy_version
        self.entry_fee = fee
        self.entry_slippage = slippage_loss
        self.mfe = Decimal("0")
        self.mae = Decimal("0")

        # Deduct entry fee and slippage accounting
        self.cumulative_fees += fee
        self.cumulative_slippage += slippage_loss
        self.cash -= fee
        self.cash -= slippage_loss

        # Calculate initial margin lock if applicable
        notional = effective_price * Decimal(quantity) * multiplier
        self.notional_exposure = notional
        if self.margin_rate > Decimal("0"):
            self.margin_used = notional * self.margin_rate
            self.cash -= self.margin_used

    def close_position(
        self,
        reference_exit_price: Decimal,
        effective_exit_price: Decimal,
        timestamp: datetime,
        exit_reason: str,
        exit_fee: Decimal,
        exit_slippage_loss: Decimal,
        multiplier: Decimal = Decimal("1"),
        is_forced_close: bool = False,
    ) -> BacktestTrade:
        """
        Close the open position and record an immutable BacktestTrade.
        Enforces position existence and non-negative quantity invariants.
        """
        if not self.has_open_position:
            raise BacktestValidationError("Cannot close position: No position is currently open")
        if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
            raise BacktestValidationError(f"Exit timestamp must be UTC: {timestamp}")
        if self.entry_timestamp is not None and timestamp < self.entry_timestamp:
            raise BacktestValidationError(
                f"Exit timestamp ({timestamp}) cannot precede "
                f"entry timestamp ({self.entry_timestamp})"
            )

        assert self.position_side is not None
        assert self.position_symbol is not None
        assert self.entry_timestamp is not None
        assert self.entry_signal_id is not None
        assert self.strategy_version is not None

        qty = self.position_quantity
        dec_qty = Decimal(qty)

        # Gross PnL using reference prices (before friction)
        if self.position_side == TradeSide.LONG:
            ref_gross_pnl = (
                (reference_exit_price - self.entry_reference_price) * dec_qty * multiplier
            )
        else:
            ref_gross_pnl = (
                (self.entry_reference_price - reference_exit_price) * dec_qty * multiplier
            )

        total_trade_fee = self.entry_fee + exit_fee
        total_trade_slippage = self.entry_slippage + exit_slippage_loss
        total_trade_friction = total_trade_fee + total_trade_slippage

        net_pnl = ref_gross_pnl - total_trade_friction

        # Return percentage relative to initial trade notional
        entry_notional = self.entry_effective_price * dec_qty * multiplier
        return_pct = (
            (net_pnl / entry_notional) * Decimal("100") if entry_notional > 0 else Decimal("0")
        )

        holding_duration = int((timestamp - self.entry_timestamp).total_seconds())

        trade_id = f"TRD-{self.entry_signal_id}-{len(self.completed_trades) + 1:04d}"

        trade = BacktestTrade(
            trade_id=trade_id,
            symbol=self.position_symbol,
            side=self.position_side,
            entry_timestamp=self.entry_timestamp,
            entry_price=self.entry_effective_price,
            entry_quantity=qty,
            exit_timestamp=timestamp,
            exit_price=effective_exit_price,
            exit_quantity=qty,
            gross_pnl=ref_gross_pnl,
            fees=total_trade_fee,
            slippage=total_trade_slippage,
            other_costs=Decimal("0"),
            net_pnl=net_pnl,
            return_pct=return_pct,
            holding_duration_seconds=holding_duration,
            max_favorable_excursion=self.mfe,
            max_adverse_excursion=self.mae,
            entry_signal_id=self.entry_signal_id,
            strategy_version=self.strategy_version,
            exit_reason=exit_reason,
            is_forced_close=is_forced_close,
        )

        # Update portfolio ledger
        self.completed_trades.append(trade)
        self.cumulative_realized_pnl += ref_gross_pnl
        self.cumulative_fees += exit_fee
        self.cumulative_slippage += exit_slippage_loss
        self.cash -= exit_fee
        self.cash += (
            ref_gross_pnl - exit_slippage_loss
        )  # Cash gains gross trade pnl minus exit slippage (entry fee/slippage already deducted)

        # Release margin
        self.cash += self.margin_used
        self.margin_used = Decimal("0")

        # Reset position state
        self.has_open_position = False
        self.position_symbol = None
        self.position_side = None
        self.position_quantity = 0
        self.unrealized_pnl = Decimal("0")
        self.notional_exposure = Decimal("0")
        self.mfe = Decimal("0")
        self.mae = Decimal("0")

        return trade

    def update_bar(
        self,
        candle: MarketCandle,
        multiplier: Decimal = Decimal("1"),
    ) -> EquitySnapshot:
        """
        Update portfolio state for a closed bar:
        - Updates continuous MFE/MAE if position is open
        - Updates unrealized PnL at bar close
        - Computes equity, peak equity, and drawdown
        - Records and returns an EquitySnapshot
        """
        ts = candle.exchange_timestamp

        if self.has_open_position:
            assert self.position_side is not None
            entry_p = self.entry_reference_price
            high_p = candle.high
            low_p = candle.low
            close_p = candle.close
            dec_qty = Decimal(self.position_quantity)

            # Continuous MFE and MAE tracking
            if self.position_side == TradeSide.LONG:
                favorable = max(Decimal("0"), high_p - entry_p)
                adverse = max(Decimal("0"), entry_p - low_p)
                unrealized = (close_p - entry_p) * dec_qty * multiplier
            else:
                favorable = max(Decimal("0"), entry_p - low_p)
                adverse = max(Decimal("0"), high_p - entry_p)
                unrealized = (entry_p - close_p) * dec_qty * multiplier

            if favorable > self.mfe:
                self.mfe = favorable
            if adverse > self.mae:
                self.mae = adverse

            self.unrealized_pnl = unrealized
            self.notional_exposure = close_p * dec_qty * multiplier
        else:
            self.unrealized_pnl = Decimal("0")
            self.notional_exposure = Decimal("0")

        curr_equity = self.equity

        # Peak and drawdown calculation
        if curr_equity > self.peak_equity:
            self.peak_equity = curr_equity
            self._drawdown_start_ts = None
            drawdown = Decimal("0")
            drawdown_pct = Decimal("0")
        else:
            drawdown = self.peak_equity - curr_equity
            drawdown_pct = (
                (drawdown / self.peak_equity) if self.peak_equity > Decimal("0") else Decimal("0")
            )
            if self._drawdown_start_ts is None:
                self._drawdown_start_ts = ts
            dd_duration = int((ts - self._drawdown_start_ts).total_seconds())
            if dd_duration > self.max_drawdown_duration_seconds:
                self.max_drawdown_duration_seconds = dd_duration

        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
        if drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = drawdown_pct

        snapshot = EquitySnapshot(
            timestamp=ts,
            equity=curr_equity,
            cash=self.cash,
            margin_used=self.margin_used,
            realized_pnl=self.cumulative_realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            cumulative_fees=self.cumulative_fees,
            cumulative_slippage=self.cumulative_slippage,
            notional_exposure=self.notional_exposure,
            drawdown=drawdown,
            drawdown_pct=drawdown_pct,
        )
        self.equity_snapshots.append(snapshot)
        return snapshot
