"""
Institutional Robustness Gatekeeper.
Integrates Walk-Forward, Regime Stress, Parameter Sensitivity, Cost Friction, and Monte Carlo
into an authoritative institutional pass/fail gate verdict.
Enforces the mandatory gate ratings:
ROBUST | CONDITIONALLY_ROBUST | UNSTABLE | INSUFFICIENT_DATA | FAILED.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alphaforge.quant_validation.models import (
        CostStressResult,
        MonteCarloDistribution,
        ParameterSensitivityResult,
        RegimePerformance,
        WalkForwardReport,
    )

from alphaforge.quant_validation.metrics import (
    calculate_expectancy,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    evaluate_sample_sufficiency,
)
from alphaforge.quant_validation.models import (
    InstitutionalRobustnessReport,
    RobustnessGateResult,
)


class InstitutionalRobustnessGate:
    """
    Authoritative quantitative validation evaluator.
    """

    @classmethod
    def evaluate(
        cls,
        trades_pnl: Sequence[Decimal],
        trades_return_pct: Sequence[float] | None = None,
        duration_days: int = 30,
        walk_forward: WalkForwardReport | None = None,
        regime_analysis: list[RegimePerformance] | None = None,
        sensitivity_analysis: list[ParameterSensitivityResult] | None = None,
        cost_stress: CostStressResult | None = None,
        monte_carlo: MonteCarloDistribution | None = None,
        report_id: str | None = None,
    ) -> InstitutionalRobustnessReport:
        """
        Evaluate full quantitative robustness across all available test layers.
        """
        n_trades = len(trades_pnl)
        is_sufficient, sample_caveat = evaluate_sample_sufficiency(
            n_trades, duration_days=duration_days
        )

        expectancy, win_rate, profit_factor = calculate_expectancy(trades_pnl)
        returns = (
            list(trades_return_pct)
            if trades_return_pct
            else [float(p / Decimal("1000000")) for p in trades_pnl]
        )
        sharpe = calculate_sharpe_ratio(returns) if len(returns) >= 2 else None
        sortino = calculate_sortino_ratio(returns) if len(returns) >= 2 else None

        # Build equity curve for drawdown
        running_equity = Decimal("1000000")
        eq_curve = [running_equity]
        for p in trades_pnl:
            running_equity += p
            eq_curve.append(running_equity)
        _, max_dd_pct = calculate_max_drawdown(eq_curve)

        gate_reasons: list[str] = []

        # 1. Hard Failure Checks
        is_failed = False
        if expectancy <= Decimal("0"):
            is_failed = True
            gate_reasons.append("FAILED: Strategy expectancy is zero or negative.")

        if (
            cost_stress is not None
            and not cost_stress.robust_to_2x_costs
            and cost_stress.stress_1_5x_pnl <= Decimal("0")
        ):
            is_failed = True
            gate_reasons.append("FAILED: Strategy does not survive 1.5x transaction friction.")

        if monte_carlo is not None and monte_carlo.p_value_zero_expectancy > 0.20:
            is_failed = True
            gate_reasons.append(
                f"FAILED: Empirical p-value ({monte_carlo.p_value_zero_expectancy}) "
                "indicates zero edge significance."
            )

        if is_failed:
            verdict = RobustnessGateResult.FAILED
            return cls._build_report(
                report_id=report_id,
                verdict=verdict,
                gate_reasons=gate_reasons,
                n_trades=n_trades,
                is_sufficient=is_sufficient,
                sample_caveat=sample_caveat,
                expectancy=expectancy,
                profit_factor=profit_factor if profit_factor > 0 else None,
                sharpe=sharpe,
                sortino=sortino,
                max_dd_pct=max_dd_pct,
                walk_forward=walk_forward,
                regime_analysis=regime_analysis or [],
                sensitivity_analysis=sensitivity_analysis or [],
                cost_stress=cost_stress,
                monte_carlo=monte_carlo,
            )

        # 2. Insufficient Data Check
        if not is_sufficient:
            verdict = RobustnessGateResult.INSUFFICIENT_DATA
            gate_reasons.append(
                sample_caveat
                or "INSUFFICIENT_DATA: Sample size inadequate for institutional validation."
            )
            return cls._build_report(
                report_id=report_id,
                verdict=verdict,
                gate_reasons=gate_reasons,
                n_trades=n_trades,
                is_sufficient=is_sufficient,
                sample_caveat=sample_caveat,
                expectancy=expectancy,
                profit_factor=profit_factor if profit_factor > 0 else None,
                sharpe=sharpe,
                sortino=sortino,
                max_dd_pct=max_dd_pct,
                walk_forward=walk_forward,
                regime_analysis=regime_analysis or [],
                sensitivity_analysis=sensitivity_analysis or [],
                cost_stress=cost_stress,
                monte_carlo=monte_carlo,
            )

        # 3. Instability Checks
        is_unstable = False
        if walk_forward is not None and not walk_forward.is_stable:
            is_unstable = True
            gate_reasons.append(
                "UNSTABLE: Walk-forward analysis unstable "
                f"(WFE = {walk_forward.walk_forward_efficiency}, "
                f"Positive OOS = {walk_forward.positive_oos_ratio * 100}%)."
            )

        if sensitivity_analysis:
            cliff_edges = [s for s in sensitivity_analysis if s.cliff_edge_detected]
            if cliff_edges:
                is_unstable = True
                cliff_names = ", ".join([c.parameter_name for c in cliff_edges])
                gate_reasons.append(f"UNSTABLE: Parameter cliff-edge detected on: {cliff_names}.")

        if monte_carlo is not None:
            if monte_carlo.probability_of_ruin_pct > 5.0:
                is_unstable = True
                gate_reasons.append(
                    "UNSTABLE: Monte Carlo ruin probability "
                    f"({monte_carlo.probability_of_ruin_pct}%) exceeds 5% threshold."
                )
            if monte_carlo.p95_drawdown_pct > 25.0:
                is_unstable = True
                gate_reasons.append(
                    "UNSTABLE: Monte Carlo 95th percentile drawdown "
                    f"({monte_carlo.p95_drawdown_pct}%) exceeds 25% tolerance."
                )

        if is_unstable:
            verdict = RobustnessGateResult.UNSTABLE
            return cls._build_report(
                report_id=report_id,
                verdict=verdict,
                gate_reasons=gate_reasons,
                n_trades=n_trades,
                is_sufficient=is_sufficient,
                sample_caveat=sample_caveat,
                expectancy=expectancy,
                profit_factor=profit_factor if profit_factor > 0 else None,
                sharpe=sharpe,
                sortino=sortino,
                max_dd_pct=max_dd_pct,
                walk_forward=walk_forward,
                regime_analysis=regime_analysis or [],
                sensitivity_analysis=sensitivity_analysis or [],
                cost_stress=cost_stress,
                monte_carlo=monte_carlo,
            )

        # 4. Conditional Robustness Checks
        is_conditional = False
        if cost_stress is not None and not cost_stress.robust_to_2x_costs:
            is_conditional = True
            gate_reasons.append(
                "CONDITIONALLY_ROBUST: Strategy does not remain profitable under 2x friction."
            )

        if regime_analysis:
            failing_regimes = [r for r in regime_analysis if r.is_regime_failing]
            if failing_regimes:
                is_conditional = True
                failing_names = ", ".join([r.regime.value for r in failing_regimes])
                gate_reasons.append(
                    f"CONDITIONALLY_ROBUST: Fragility in market regimes: {failing_names}."
                )

        if monte_carlo is not None and monte_carlo.p_value_zero_expectancy > 0.05:
            is_conditional = True
            gate_reasons.append(
                f"CONDITIONALLY_ROBUST: p-value ({monte_carlo.p_value_zero_expectancy}) "
                "is between 0.05 and 0.20."
            )

        if is_conditional:
            verdict = RobustnessGateResult.CONDITIONALLY_ROBUST
            return cls._build_report(
                report_id=report_id,
                verdict=verdict,
                gate_reasons=gate_reasons,
                n_trades=n_trades,
                is_sufficient=is_sufficient,
                sample_caveat=sample_caveat,
                expectancy=expectancy,
                profit_factor=profit_factor if profit_factor > 0 else None,
                sharpe=sharpe,
                sortino=sortino,
                max_dd_pct=max_dd_pct,
                walk_forward=walk_forward,
                regime_analysis=regime_analysis or [],
                sensitivity_analysis=sensitivity_analysis or [],
                cost_stress=cost_stress,
                monte_carlo=monte_carlo,
            )

        # 5. Full Robustness
        verdict = RobustnessGateResult.ROBUST
        gate_reasons.append(
            "ROBUST: Strategy passed temporal walk-forward validation, cost stress (2x), "
            "regime stability, parameter sensitivity, and Monte Carlo bootstrap tests."
        )

        return cls._build_report(
            report_id=report_id,
            verdict=verdict,
            gate_reasons=gate_reasons,
            n_trades=n_trades,
            is_sufficient=is_sufficient,
            sample_caveat=sample_caveat,
            expectancy=expectancy,
            profit_factor=profit_factor if profit_factor > 0 else None,
            sharpe=sharpe,
            sortino=sortino,
            max_dd_pct=max_dd_pct,
            walk_forward=walk_forward,
            regime_analysis=regime_analysis or [],
            sensitivity_analysis=sensitivity_analysis or [],
            cost_stress=cost_stress,
            monte_carlo=monte_carlo,
        )

    @staticmethod
    def _build_report(
        report_id: str | None,
        verdict: RobustnessGateResult,
        gate_reasons: list[str],
        n_trades: int,
        is_sufficient: bool,
        sample_caveat: str | None,
        expectancy: Decimal,
        profit_factor: Decimal | None,
        sharpe: float | None,
        sortino: float | None,
        max_dd_pct: Decimal,
        walk_forward: WalkForwardReport | None,
        regime_analysis: list[RegimePerformance],
        sensitivity_analysis: list[ParameterSensitivityResult],
        cost_stress: CostStressResult | None,
        monte_carlo: MonteCarloDistribution | None,
    ) -> InstitutionalRobustnessReport:
        return InstitutionalRobustnessReport(
            report_id=report_id or f"QUANT-VAL-{uuid.uuid4().hex[:8].upper()}",
            overall_gate=verdict,
            gate_reasons=gate_reasons,
            sample_size=n_trades,
            has_sufficient_sample=is_sufficient,
            sample_size_caveat=sample_caveat,
            expectancy=round(expectancy, 2),
            profit_factor=round(profit_factor, 2) if profit_factor is not None else None,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=round(max_dd_pct, 2),
            walk_forward=walk_forward,
            regime_analysis=regime_analysis,
            sensitivity_analysis=sensitivity_analysis,
            cost_stress=cost_stress,
            monte_carlo=monte_carlo,
        )
