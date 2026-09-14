"""
Parameter Sensitivity and Cliff-Edge Fragility Analysis.
Tests parameter robustness by perturbing strategy thresholds (+-5%, +-10%, +-20%, +-30%).
Detects fragile cliff-edges where minor parameter shifts lead to catastrophic collapse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from alphaforge.quant_validation.models import ParameterSensitivityResult


class ParameterSensitivityAnalyzer:
    """
    Evaluates sensitivity of strategy metrics to variations in parameters.
    """

    @staticmethod
    def analyze_parameter(
        parameter_name: str,
        baseline_value: float,
        eval_fn: Callable[[float], float],
        perturbations_pct: tuple[float, ...] = (
            -30.0,
            -20.0,
            -10.0,
            -5.0,
            5.0,
            10.0,
            20.0,
            30.0,
        ),
    ) -> ParameterSensitivityResult:
        """
        Perturbs parameter by specified percentages and monitors degradation against baseline.
        Cliff-edge detected if a <= 10% change causes >= 40% degradation.
        """
        baseline_metric = eval_fn(baseline_value)
        perturbation_records: list[dict[str, Any]] = []

        cliff_edge_detected = False
        max_degradation_pct = 0.0
        gradients: list[float] = []

        for delta in perturbations_pct:
            perturbed_val = baseline_value * (1.0 + (delta / 100.0))
            metric_val = eval_fn(perturbed_val)

            # Compute degradation relative to baseline
            if baseline_metric > 0:
                deg_pct = ((baseline_metric - metric_val) / baseline_metric) * 100.0
            else:
                deg_pct = 0.0

            if deg_pct > max_degradation_pct:
                max_degradation_pct = deg_pct

            # Gradient: |change in metric / relative change in param|
            param_delta_rel = abs(delta / 100.0)
            denom = param_delta_rel * baseline_value
            gradient = (
                abs(metric_val - baseline_metric) / denom if denom > 0 else 0.0
            )
            gradients.append(gradient)

            # Cliff edge: small shift (<= 10%) producing severe collapse (>= 40%)
            if abs(delta) <= 10.0 and deg_pct >= 40.0:
                cliff_edge_detected = True

            perturbation_records.append(
                {
                    "delta_pct": delta,
                    "value": round(perturbed_val, 4),
                    "metric_value": round(metric_val, 4),
                    "degradation_pct": round(deg_pct, 2),
                }
            )

        mean_gradient = sum(gradients) / len(gradients) if gradients else 0.0

        return ParameterSensitivityResult(
            parameter_name=parameter_name,
            baseline_value=baseline_value,
            perturbations=perturbation_records,
            cliff_edge_detected=cliff_edge_detected,
            max_metric_degradation_pct=round(max_degradation_pct, 2),
            sensitivity_gradient=round(mean_gradient, 4),
        )
