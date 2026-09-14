"""
Cost Sensitivity and Execution Friction Stress Engine.
Tests strategy viability under multiplied transaction costs (brokerage, exchange taxes, STT)
and expanded bid-ask spreads and adverse slippage.
Calculates break-even slippage in basis points.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

from alphaforge.quant_validation.models import CostStressResult


class CostSensitivityEngine:
    """
    Simulates increased transaction costs and slippage on completed trade logs.
    """

    @staticmethod
    def stress_test_trades(
        trades: Sequence[
            dict[str, Any]
        ],  # list of trades with "gross_pnl", "fees", "slippage", "volume_inr"
    ) -> CostStressResult:
        """
        Applies friction stress multipliers:
        - 1.5x fees and slippage
        - 2.0x fees and slippage
        - 3.0x fees and slippage
        Determines the break-even adverse slippage in basis points (bps) of trade turnover.
        """
        total_gross = sum((Decimal(str(t.get("gross_pnl", 0))) for t in trades), Decimal("0"))
        baseline_fees = sum((Decimal(str(t.get("fees", 0))) for t in trades), Decimal("0"))
        baseline_slippage = sum((Decimal(str(t.get("slippage", 0))) for t in trades), Decimal("0"))
        baseline_net = total_gross - baseline_fees - baseline_slippage

        stress_1_5x_pnl = (
            total_gross - (baseline_fees * Decimal("1.5")) - (baseline_slippage * Decimal("1.5"))
        )
        stress_2_0x_pnl = (
            total_gross - (baseline_fees * Decimal("2.0")) - (baseline_slippage * Decimal("2.0"))
        )
        stress_3_0x_pnl = (
            total_gross - (baseline_fees * Decimal("3.0")) - (baseline_slippage * Decimal("3.0"))
        )

        robust_2x = stress_2_0x_pnl > Decimal("0")
        robust_3x = stress_3_0x_pnl > Decimal("0")

        # Total notional turnover across all trades
        total_turnover = sum(
            (Decimal(str(t.get("turnover", t.get("volume_inr", 100000)))) for t in trades),
            Decimal("0"),
        )

        break_even_bps: float | None = None
        if total_turnover > Decimal("0"):
            # Available profit margin after baseline fees
            margin_after_fees = total_gross - baseline_fees
            if margin_after_fees > Decimal("0"):
                # Max slippage before PnL becomes 0
                max_tolerable_slippage_pct = margin_after_fees / total_turnover
                break_even_bps = float(max_tolerable_slippage_pct * Decimal("10000"))
            else:
                break_even_bps = 0.0

        return CostStressResult(
            baseline_fees=baseline_fees,
            baseline_slippage=baseline_slippage,
            baseline_net_pnl=baseline_net,
            stress_1_5x_pnl=stress_1_5x_pnl,
            stress_2_0x_pnl=stress_2_0x_pnl,
            stress_3_0x_pnl=stress_3_0x_pnl,
            break_even_slippage_bps=round(break_even_bps, 2)
            if break_even_bps is not None
            else None,
            robust_to_2x_costs=robust_2x,
            robust_to_3x_costs=robust_3x,
        )
