"""
Monte Carlo and Bootstrap Validation Engine.
Performs:
1. Trade-order permutation bootstrap: simulates sequence risk and computes drawdowns.
2. Return resampling with replacement: computes empirical 95% CI for Sharpe ratio.
3. Probability of ruin: likelihood of equity suffering catastrophic drawdown (> 20%).
4. Statistical hypothesis testing: empirical p-value for zero/negative expectancy.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from alphaforge.quant_validation.metrics import calculate_max_drawdown, calculate_sharpe_ratio
from alphaforge.quant_validation.models import MonteCarloDistribution


class MonteCarloEngine:
    """
    Simulates thousands of alternative paths using historical trade logs.
    """

    def __init__(self, iterations: int = 1000, seed: int = 42) -> None:
        self.iterations = max(100, iterations)
        self.seed = seed

    def run_validation(
        self,
        trades_pnl: Sequence[Decimal],
        trades_return_pct: Sequence[float] | None = None,
        initial_capital: Decimal = Decimal("1000000"),
        ruin_threshold_pct: float = 20.0,
    ) -> MonteCarloDistribution:
        """
        Executes sequence shuffling and return bootstrap.
        """
        if not trades_pnl:
            return MonteCarloDistribution(
                iterations=self.iterations,
                trade_count=0,
                mean_drawdown_pct=0.0,
                p95_drawdown_pct=0.0,
                p99_drawdown_pct=0.0,
                max_simulated_drawdown_pct=0.0,
                probability_of_ruin_pct=0.0,
                sharpe_ci_lower=None,
                sharpe_ci_upper=None,
                p_value_zero_expectancy=1.0,
            )

        pnl_list = list(trades_pnl)
        ret_list = (
            list(trades_return_pct)
            if trades_return_pct
            else [float(p / initial_capital) for p in pnl_list]
        )

        rng = random.Random(self.seed)  # noqa: S311

        # 1. Sequence Shuffling (Permutation without replacement) to evaluate sequence risk
        drawdown_pcts: list[float] = []
        ruin_count = 0

        for _ in range(self.iterations):
            shuffled_pnl = list(pnl_list)
            rng.shuffle(shuffled_pnl)

            running_eq = initial_capital
            eq_curve = [running_eq]
            for p in shuffled_pnl:
                running_eq += p
                eq_curve.append(running_eq)

            _, max_dd_pct = calculate_max_drawdown(eq_curve)
            dd_val = float(max_dd_pct)
            drawdown_pcts.append(dd_val)

            if dd_val >= ruin_threshold_pct:
                ruin_count += 1

        drawdown_pcts.sort()
        mean_dd = sum(drawdown_pcts) / len(drawdown_pcts)
        p95_idx = int(0.95 * len(drawdown_pcts))
        p99_idx = int(0.99 * len(drawdown_pcts))
        p95_dd = drawdown_pcts[min(p95_idx, len(drawdown_pcts) - 1)]
        p99_dd = drawdown_pcts[min(p99_idx, len(drawdown_pcts) - 1)]
        max_sim_dd = drawdown_pcts[-1]
        prob_ruin = (ruin_count / self.iterations) * 100.0

        # 2. Return Bootstrap (Resampling with replacement) for Sharpe CI
        bootstrapped_sharpes: list[float] = []
        n_trades = len(ret_list)

        if n_trades >= 5:
            for _ in range(self.iterations):
                sample = [rng.choice(ret_list) for _ in range(n_trades)]
                s = calculate_sharpe_ratio(sample)
                if s is not None:
                    bootstrapped_sharpes.append(s)

        sharpe_lower: float | None = None
        sharpe_upper: float | None = None
        if len(bootstrapped_sharpes) >= 50:
            bootstrapped_sharpes.sort()
            ci_low_idx = int(0.025 * len(bootstrapped_sharpes))
            ci_high_idx = int(0.975 * len(bootstrapped_sharpes))
            sharpe_lower = round(bootstrapped_sharpes[ci_low_idx], 4)
            sharpe_upper = round(
                bootstrapped_sharpes[min(ci_high_idx, len(bootstrapped_sharpes) - 1)], 4
            )

        # 3. Hypothesis Testing: p-value for H0: mean(PnL) <= 0
        mean_pnl = sum(pnl_list, Decimal("0")) / Decimal(len(pnl_list))
        if len(pnl_list) < 2:
            p_val = 1.0
        else:
            variance = sum(float(p - mean_pnl) ** 2 for p in pnl_list) / (len(pnl_list) - 1)
            std_pnl = Decimal(str(math.sqrt(variance)))
            if std_pnl > Decimal("0"):
                denom = std_pnl / Decimal(str(math.sqrt(len(pnl_list))))
                t_stat = float(mean_pnl / denom)
                p_val = 1.0 if t_stat <= 0 else 0.5 * math.erfc(t_stat / math.sqrt(2.0))
            else:
                p_val = 0.0 if mean_pnl > Decimal("0") else 1.0

        return MonteCarloDistribution(
            iterations=self.iterations,
            trade_count=len(pnl_list),
            mean_drawdown_pct=round(mean_dd, 2),
            p95_drawdown_pct=round(p95_dd, 2),
            p99_drawdown_pct=round(p99_dd, 2),
            max_simulated_drawdown_pct=round(max_sim_dd, 2),
            probability_of_ruin_pct=round(prob_ruin, 2),
            sharpe_ci_lower=sharpe_lower,
            sharpe_ci_upper=sharpe_upper,
            p_value_zero_expectancy=round(p_val, 4),
        )
