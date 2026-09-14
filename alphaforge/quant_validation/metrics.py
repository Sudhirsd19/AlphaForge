"""
Quantitative performance and risk metrics calculations with institutional sample-size checks.
Enforces rigorous mathematical definitions:
- Expectancy = (Win_Rate * Avg_Win) - (Loss_Rate * Avg_Loss)
- Profit Factor = Sum(Wins) / Sum(Losses)
- Annualized Sharpe Ratio = (Mean(R) - Rf) / StdDev(R) * sqrt(252)
- Annualized Sortino Ratio = (Mean(R) - Rf) / DownsideStdDev(R) * sqrt(252)
- Sample size caveat flagged whenever sample count N < 30.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def evaluate_sample_sufficiency(n: int, duration_days: int = 30) -> tuple[bool, str | None]:
    """
    Check if sample count and duration satisfy institutional statistical significance.
    Returns (is_sufficient, caveat_message).
    """
    if n < 30:
        return (
            False,
            f"Sample size N = {n} is below institutional minimum (N >= 30). "
            "Ratios and statistics have high variance and must not be considered conclusive.",
        )
    if duration_days < 30:
        return (
            False,
            f"Evaluation duration ({duration_days} days) is less than 30-day minimum. "
            "Seasonal and multi-regime coverage is incomplete.",
        )
    return True, None


def calculate_expectancy(trades_pnl: Sequence[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate expectancy: (WinRate * AvgWin) - (LossRate * AvgLoss).
    Returns (expectancy, win_rate, profit_factor).
    """
    if not trades_pnl:
        return Decimal("0"), Decimal("0"), Decimal("0")

    wins = [p for p in trades_pnl if p > Decimal("0")]
    losses = [abs(p) for p in trades_pnl if p < Decimal("0")]

    total_count = Decimal(len(trades_pnl))
    n_wins = Decimal(len(wins))
    n_losses = Decimal(len(losses))

    win_rate = n_wins / total_count
    loss_rate = n_losses / total_count

    avg_win = (sum(wins, Decimal("0")) / n_wins) if n_wins > 0 else Decimal("0")
    avg_loss = (sum(losses, Decimal("0")) / n_losses) if n_losses > 0 else Decimal("0")

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    sum_wins = sum(wins, Decimal("0"))
    sum_losses = sum(losses, Decimal("0"))
    profit_factor = (sum_wins / sum_losses) if sum_losses > Decimal("0") else Decimal("0")

    return expectancy, win_rate, profit_factor


def calculate_sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate_annual: float = 0.065,  # 6.5% Indian T-bill baseline
    periods_per_year: int = 252,
) -> float | None:
    """
    Calculate annualized Sharpe ratio from daily/periodic returns.
    Requires at least 2 observations to compute sample standard deviation (ddof=1).
    """
    if len(returns) < 2:
        return None

    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev < 1e-9:
        return 0.0

    rf_periodic = risk_free_rate_annual / periods_per_year
    excess_return = mean_r - rf_periodic
    annualized_sharpe = (excess_return / std_dev) * math.sqrt(periods_per_year)
    return round(annualized_sharpe, 4)


def calculate_sortino_ratio(
    returns: Sequence[float],
    target_return: float = 0.0,
    periods_per_year: int = 252,
) -> float | None:
    """
    Calculate annualized Sortino ratio using downside semi-variance.
    """
    if len(returns) < 2:
        return None

    mean_r = sum(returns) / len(returns)
    downside_diffs = [min(0.0, r - target_return) ** 2 for r in returns]
    downside_variance = sum(downside_diffs) / len(returns)
    downside_dev = math.sqrt(downside_variance)

    if downside_dev < 1e-9:
        return None

    excess_return = mean_r - target_return
    annualized_sortino = (excess_return / downside_dev) * math.sqrt(periods_per_year)
    return round(annualized_sortino, 4)


def calculate_max_drawdown(equity_series: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """
    Calculate absolute maximum drawdown and percentage drawdown from an equity curve.
    Returns (max_dd_amount, max_dd_pct).
    """
    if not equity_series:
        return Decimal("0"), Decimal("0")

    peak = equity_series[0]
    max_dd = Decimal("0")
    max_dd_pct = Decimal("0")

    for eq in equity_series:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
        if peak > Decimal("0"):
            dd_pct = (dd / peak) * Decimal("100")
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

    return max_dd, max_dd_pct
