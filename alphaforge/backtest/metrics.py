"""
AlphaForge Quantitative Performance Metrics Engine.
Computes return, trade statistics, risk-adjusted metrics (Sharpe, Sortino, Calmar),
drawdown, and exposure metrics with Decimal financial precision and explicit
insufficient-sample guards.
"""

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from alphaforge.backtest.models import BacktestMetrics, BacktestTrade, EquitySnapshot


def calculate_backtest_metrics(
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquitySnapshot],
    initial_capital: Decimal,
    start_time: datetime,
    end_time: datetime,
) -> BacktestMetrics:
    """
    Compute comprehensive quantitative performance and risk metrics.
    Guarantees that empty or tiny samples produce explicit None values for ratios
    rather than misleading annualized statistics.
    """
    total_trades = len(trades)

    # Financial Totals across completed trades
    gross_pnl = sum((t.gross_pnl for t in trades), Decimal("0"))
    total_fees = sum((t.fees for t in trades), Decimal("0"))
    total_slippage = sum((t.slippage for t in trades), Decimal("0"))
    net_pnl = gross_pnl - total_fees - total_slippage

    # Total Return
    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    total_return = final_equity - initial_capital
    total_return_pct = (
        (total_return / initial_capital) * Decimal("100") if initial_capital > 0 else Decimal("0")
    )

    # Sample duration
    duration_days = max(
        Decimal("1"), Decimal(str((end_time - start_time).total_seconds())) / Decimal("86400")
    )
    is_sample_sufficient = duration_days >= Decimal("30") and len(equity_curve) >= 30

    # Annualized Return
    annualized_return_pct: Decimal | None = None
    if is_sample_sufficient:
        # Standard compound annual growth rate
        cagr_exponent = float(Decimal("365.25") / duration_days)
        base_return = float(final_equity / initial_capital)
        if base_return > 0:
            try:
                annualized_val = (pow(base_return, cagr_exponent) - 1.0) * 100.0
                annualized_return_pct = Decimal(str(round(annualized_val, 4)))
            except (ValueError, OverflowError):
                annualized_return_pct = None

    # Trade Statistics
    winning_trades = [t for t in trades if t.net_pnl > Decimal("0")]
    losing_trades = [t for t in trades if t.net_pnl < Decimal("0")]
    break_even_trades = [t for t in trades if t.net_pnl == Decimal("0")]

    n_win = len(winning_trades)
    n_loss = len(losing_trades)
    n_be = len(break_even_trades)

    dec_total_trades = Decimal(total_trades)
    win_rate = Decimal(n_win) / dec_total_trades if total_trades > 0 else Decimal("0")
    loss_rate = Decimal(n_loss) / dec_total_trades if total_trades > 0 else Decimal("0")

    sum_win_pnl = sum((t.net_pnl for t in winning_trades), Decimal("0"))
    sum_loss_pnl = sum((abs(t.net_pnl) for t in losing_trades), Decimal("0"))

    average_win = (sum_win_pnl / Decimal(n_win)) if n_win > 0 else Decimal("0")
    average_loss = (sum_loss_pnl / Decimal(n_loss)) if n_loss > 0 else Decimal("0")

    payoff_ratio: Decimal | None = None
    if average_loss > Decimal("0"):
        payoff_ratio = average_win / average_loss

    profit_factor: Decimal | None = None
    if sum_loss_pnl > Decimal("0"):
        profit_factor = sum_win_pnl / sum_loss_pnl

    expectancy = (win_rate * average_win) - (loss_rate * average_loss)

    # Risk Metrics from Equity Curve
    max_dd = Decimal("0")
    max_dd_pct = Decimal("0")
    max_dd_dur = 0
    avg_exposure = Decimal("0")
    max_exposure = Decimal("0")

    if equity_curve:
        max_dd = max((s.drawdown for s in equity_curve), default=Decimal("0"))
        max_dd_pct = max((s.drawdown_pct for s in equity_curve), default=Decimal("0"))
        max_exposure = max((s.notional_exposure for s in equity_curve), default=Decimal("0"))
        avg_exposure = sum((s.notional_exposure for s in equity_curve), Decimal("0")) / Decimal(
            len(equity_curve)
        )

        # Peak drawdown duration
        peak_eq = initial_capital
        curr_dd_start: datetime | None = None
        for s in equity_curve:
            if s.equity >= peak_eq:
                peak_eq = s.equity
                curr_dd_start = None
            else:
                if curr_dd_start is None:
                    curr_dd_start = s.timestamp
                dur = int((s.timestamp - curr_dd_start).total_seconds())
                if dur > max_dd_dur:
                    max_dd_dur = dur

    # Volatility, Sharpe, Sortino, Calmar
    volatility_annualized: Decimal | None = None
    downside_vol_annualized: Decimal | None = None
    sharpe_ratio: Decimal | None = None
    sortino_ratio: Decimal | None = None
    calmar_ratio: Decimal | None = None

    if is_sample_sufficient and len(equity_curve) >= 2:
        # Calculate periodic returns from equity curve
        returns: list[float] = []
        for i in range(1, len(equity_curve)):
            prev_eq = float(equity_curve[i - 1].equity)
            curr_eq = float(equity_curve[i].equity)
            if prev_eq > 0:
                returns.append((curr_eq - prev_eq) / prev_eq)
            else:
                returns.append(0.0)

        if len(returns) >= 30:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            vol = math.sqrt(variance) if variance > 0 else 0.0

            downside_sq = [min(0.0, r) ** 2 for r in returns]
            downside_var = sum(downside_sq) / len(returns)
            downside_vol = math.sqrt(downside_var) if downside_var > 0 else 0.0

            # Assuming 252 annual periods factor (or annualized by periods per year)
            annual_factor = math.sqrt(252.0 * (len(returns) / max(1.0, float(duration_days))))
            ann_vol = vol * annual_factor
            ann_downside = downside_vol * annual_factor

            if ann_vol > 0:
                volatility_annualized = Decimal(str(round(ann_vol * 100.0, 4)))
                # Annualized Sharpe (0% risk-free rate assumption for baseline)
                ann_sharpe = ((mean_ret * (annual_factor**2)) / ann_vol) if ann_vol > 0 else 0.0
                sharpe_ratio = Decimal(str(round(ann_sharpe, 4)))

            if ann_downside > 0:
                downside_vol_annualized = Decimal(str(round(ann_downside * 100.0, 4)))
                ann_sortino = (
                    ((mean_ret * (annual_factor**2)) / ann_downside) if ann_downside > 0 else 0.0
                )
                sortino_ratio = Decimal(str(round(ann_sortino, 4)))

            if annualized_return_pct is not None and max_dd_pct > Decimal("0"):
                calmar_ratio = annualized_return_pct / (max_dd_pct * Decimal("100"))

    return BacktestMetrics(
        total_return=total_return,
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        total_trades=total_trades,
        winning_trades=n_win,
        losing_trades=n_loss,
        break_even_trades=n_be,
        win_rate=win_rate,
        loss_rate=loss_rate,
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_seconds=max_dd_dur,
        volatility_annualized=volatility_annualized,
        downside_volatility_annualized=downside_vol_annualized,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        average_exposure=avg_exposure,
        max_exposure=max_exposure,
        gross_pnl=gross_pnl,
        total_fees=total_fees,
        total_slippage=total_slippage,
        net_pnl=net_pnl,
    )
