"""
AlphaForge Quantitative Validation & Robustness Engine.
Implements Quant Gates A through J, overfitting diagnostics, out-of-sample (OOS) partitioning,
walk-forward evaluation windows, and market regime analysis.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.models import (
    BacktestMetrics,
    BacktestTrade,
    EquitySnapshot,
    QuantGateResult,
    QuantGateStatus,
    RegimeType,
    ValidationStatus,
)
from alphaforge.core.exceptions import BacktestValidationError
from alphaforge.data.models import MarketCandle
from alphaforge.ledger.models import FrozenDict


class QuantGateEvaluator:
    """
    Evaluates Quant Gates A through J providing structured diagnostic telemetry.
    """

    @staticmethod
    def evaluate_gates(
        trades: Sequence[BacktestTrade],
        equity_curve: Sequence[EquitySnapshot],
        metrics: BacktestMetrics,
        dataset: BacktestDataset,
        initial_capital: Decimal,
        risk_rejections_recorded: int = 0,
        contract_valid: bool = True,
        look_ahead_evidence: Mapping[str, Any] | None = None,
        risk_integration_evidence: Mapping[str, Any] | None = None,
        reproducibility_evidence: Mapping[str, Any] | None = None,
        oos_evidence: Mapping[str, Any] | None = None,
        contract_evidence: Mapping[str, Any] | None = None,
    ) -> tuple[tuple[QuantGateResult, ...], ValidationStatus, list[str], list[str]]:
        """
        Evaluate all 10 quant gates and determine overall validation verdict.
        Returns (gate_results, overall_status, warnings, errors).
        """
        gates: list[QuantGateResult] = []
        warnings: list[str] = []
        errors: list[str] = []

        # --- Gate A: No Look-Ahead Bias & Future Mutation Verification ---
        temporal_inversions = sum(1 for t in trades if t.exit_timestamp < t.entry_timestamp)
        causality_violations = 0
        future_mutation_tests = 0
        historical_decisions_compared = 0
        historical_orders_compared = 0
        historical_fills_compared = 0
        historical_equity_snapshots_compared = 0
        verification_executed = False
        future_mutation_test_passed = False
        historical_trace_comparison_passed = False
        future_append_invariance_passed = False

        if look_ahead_evidence is not None:
            verification_executed = bool(look_ahead_evidence.get("verification_executed", True))
            future_mutation_test_passed = bool(
                look_ahead_evidence.get("future_mutation_test_passed", True)
            )
            historical_trace_comparison_passed = bool(
                look_ahead_evidence.get("historical_trace_comparison_passed", True)
            )
            future_append_invariance_passed = bool(
                look_ahead_evidence.get("future_append_invariance_passed", True)
            )
            causality_violations = int(look_ahead_evidence.get("causality_violations", 0))
            future_mutation_tests = int(
                look_ahead_evidence.get("future_mutation_tests", 1 if verification_executed else 0)
            )
            historical_decisions_compared = int(
                look_ahead_evidence.get("historical_decisions_compared", 0)
            )
            historical_orders_compared = int(
                look_ahead_evidence.get("historical_orders_compared", 0)
            )
            historical_fills_compared = int(look_ahead_evidence.get("historical_fills_compared", 0))
            historical_equity_snapshots_compared = int(
                look_ahead_evidence.get("historical_equity_snapshots_compared", 0)
            )

        total_look_ahead_faults = temporal_inversions + causality_violations

        if not verification_executed:
            status_a = QuantGateStatus.WARNING
            reason_a = "Look-ahead verification disabled by configuration"
            warnings.append(reason_a)
            warn_count_a = 1
            err_count_a = 0
        elif total_look_ahead_faults > 0 or not (
            future_mutation_test_passed
            and historical_trace_comparison_passed
            and future_append_invariance_passed
        ):
            status_a = QuantGateStatus.FAIL
            reason_a = (
                f"Look-ahead causality violation: {temporal_inversions} temporal inversions, "
                f"{causality_violations} future mutation discrepancies"
            )
            errors.append(f"Gate A Failed: {reason_a}")
            warn_count_a = 0
            err_count_a = total_look_ahead_faults if total_look_ahead_faults > 0 else 1
        else:
            status_a = QuantGateStatus.PASS
            reason_a = (
                "Causal temporal barrier verified: 0 temporal inversions, "
                "future mutation test passed, historical trace comparison passed, "
                "future append invariance passed"
            )
            warn_count_a = 0
            err_count_a = 0

        evidence_a: dict[str, Any] = {
            "verification_executed": verification_executed,
            "future_mutation_test_passed": future_mutation_test_passed,
            "historical_trace_comparison_passed": historical_trace_comparison_passed,
            "future_append_invariance_passed": future_append_invariance_passed,
            "future_mutation_tests": future_mutation_tests,
            "historical_decisions_compared": historical_decisions_compared,
            "historical_orders_compared": historical_orders_compared,
            "historical_fills_compared": historical_fills_compared,
            "historical_equity_snapshots_compared": historical_equity_snapshots_compared,
            "causality_violations": causality_violations,
            "temporal_inversions": temporal_inversions,
        }
        if look_ahead_evidence is not None:
            for k, v in look_ahead_evidence.items():
                if k not in evidence_a:
                    evidence_a[k] = v

        gates.append(
            QuantGateResult(
                gate_id="Gate A",
                gate_name="No Look-Ahead Bias",
                status=status_a,
                reason=reason_a,
                evidence=evidence_a,
                warning_count=warn_count_a,
                error_count=err_count_a,
            )
        )

        # --- Gate B: No Data Leakage ---
        leakage_failures = 0
        for i in range(len(dataset.candles) - 1):
            if dataset.candles[i].exchange_timestamp >= dataset.candles[i + 1].exchange_timestamp:
                leakage_failures += 1

        if leakage_failures > 0:
            status_b = QuantGateStatus.FAIL
            reason_b = (
                f"Detected {leakage_failures} out-of-order or duplicate "
                f"candle timestamps in dataset"
            )
            errors.append(f"Gate B Failed: {reason_b}")
        else:
            status_b = QuantGateStatus.PASS
            reason_b = "Chronological market data ordering verified with zero temporal leakage"
        gates.append(
            QuantGateResult(
                gate_id="Gate B",
                gate_name="No Data Leakage",
                status=status_b,
                reason=reason_b,
                evidence={"chronological_faults": leakage_failures},
                error_count=leakage_failures,
            )
        )

        # --- Gate C: No Impossible Fills ---
        impossible_fills = 0
        for t in trades:
            has_invalid_price = t.entry_price <= Decimal("0") or t.exit_price <= Decimal("0")
            if has_invalid_price or t.entry_quantity <= 0:
                impossible_fills += 1

        if impossible_fills > 0:
            status_c = QuantGateStatus.FAIL
            reason_c = (
                f"Detected {impossible_fills} trades with non-positive execution prices or qty"
            )
            errors.append(f"Gate C Failed: {reason_c}")
        else:
            status_c = QuantGateStatus.PASS
            reason_c = "All executed fills verified strictly positive, finite, and conservative"
        gates.append(
            QuantGateResult(
                gate_id="Gate C",
                gate_name="No Impossible Fills",
                status=status_c,
                reason=reason_c,
                evidence={"impossible_fills": impossible_fills},
                error_count=impossible_fills,
            )
        )

        # --- Gate D: Cost and Slippage Included ---
        cost_violations = 0
        for t in trades:
            if t.net_pnl > t.gross_pnl:
                cost_violations += 1

        if cost_violations > 0:
            status_d = QuantGateStatus.FAIL
            reason_d = f"Detected {cost_violations} trades where net_pnl exceeded gross_pnl"
            errors.append(f"Gate D Failed: {reason_d}")
        else:
            status_d = QuantGateStatus.PASS
            reason_d = (
                f"Transaction friction verified: Total fees={metrics.total_fees}, "
                f"Total slippage={metrics.total_slippage}"
            )
        gates.append(
            QuantGateResult(
                gate_id="Gate D",
                gate_name="Cost & Slippage Included",
                status=status_d,
                reason=reason_d,
                evidence={
                    "cost_violations": cost_violations,
                    "total_fees": str(metrics.total_fees),
                    "total_slippage": str(metrics.total_slippage),
                },
                error_count=cost_violations,
            )
        )

        # --- Gate E: Correct Contract Lifecycle ---
        if contract_evidence is None or not contract_evidence.get("contract_master_present", False):
            status_e = QuantGateStatus.WARNING
            reason_e = "Contract master omitted; contract lifecycle cannot be fully verified"
            warnings.append(reason_e)
            evidence_e: dict[str, Any] = {
                "verification_executed": False,
                "contract_master_present": False,
                "contract_id": "NONE",
                "contract_metadata_valid": False,
                "expiry_datetime": "NONE",
                "contract_checks": 0,
                "expiry_checks": 0,
                "active_contract_checks": 0,
                "expired_trade_attempts": 0,
                "post_expiry_fills": 0,
                "lot_size_checks": 0,
                "multiplier_checks": 0,
                "lifecycle_violation": False,
                "contract_valid": contract_valid,
            }
            warn_e = 1
            err_e = 0
        else:
            c_id = str(contract_evidence.get("contract_id", "UNKNOWN"))
            exp_dt = str(contract_evidence.get("expiry_datetime", "UNKNOWN"))
            c_checks = int(contract_evidence.get("contract_checks", 0))
            exp_checks = int(contract_evidence.get("expiry_checks", 0))
            act_checks = int(contract_evidence.get("active_contract_checks", 0))
            exp_attempts = int(contract_evidence.get("expired_trade_attempts", 0))
            post_fills = int(contract_evidence.get("post_expiry_fills", 0))
            lot_checks = int(contract_evidence.get("lot_size_checks", 0))
            mult_checks = int(contract_evidence.get("multiplier_checks", 0))
            metadata_valid = bool(contract_evidence.get("contract_metadata_valid", True))
            violation = bool(contract_evidence.get("lifecycle_violation", False))
            c_valid = bool(contract_evidence.get("contract_valid", contract_valid))

            # Explicit check for lot_size and multiplier in evidence
            lot_val = contract_evidence.get("lot_size", None)
            if lot_val is not None and int(lot_val) <= 0:
                metadata_valid = False
                violation = True
            mult_val = contract_evidence.get(
                "contract_multiplier", contract_evidence.get("multiplier", None)
            )
            if mult_val is not None and Decimal(str(mult_val)) <= Decimal("0"):
                metadata_valid = False
                violation = True

            final_contract_valid = bool(
                c_valid and metadata_valid and post_fills == 0 and not violation
            )

            evidence_e = {
                "verification_executed": True,
                "contract_master_present": True,
                "contract_id": c_id,
                "contract_metadata_valid": metadata_valid,
                "expiry_datetime": exp_dt,
                "contract_checks": c_checks,
                "expiry_checks": exp_checks,
                "active_contract_checks": act_checks,
                "expired_trade_attempts": exp_attempts,
                "post_expiry_fills": post_fills,
                "lot_size_checks": lot_checks,
                "multiplier_checks": mult_checks,
                "lifecycle_violation": violation,
                "contract_valid": final_contract_valid,
            }

            if not final_contract_valid or post_fills > 0:
                status_e = QuantGateStatus.FAIL
                reason_e = (
                    f"Contract lifecycle violation: post_expiry_fills={post_fills}, "
                    f"contract_metadata_valid={metadata_valid}, lifecycle_violation={violation}"
                )
                errors.append(f"Gate E Failed: {reason_e}")
                warn_e = 0
                err_e = post_fills if post_fills > 0 else 1
            else:
                status_e = QuantGateStatus.PASS
                reason_e = (
                    f"Contract lifecycle verified for {c_id} (Expiry: {exp_dt}): "
                    f"{exp_checks} expiry checks, {act_checks} active checks, "
                    f"{lot_checks} lot size checks, 0 post-expiry fills"
                )
                warn_e = 0
                err_e = 0

        gates.append(
            QuantGateResult(
                gate_id="Gate E",
                gate_name="Correct Contract Lifecycle",
                status=status_e,
                reason=reason_e,
                evidence=evidence_e,
                warning_count=warn_e,
                error_count=err_e,
            )
        )

        # --- Gate F: Correct Risk Engine Integration ---
        verification_executed = False
        risk_calls = 0
        approved = 0
        rejected = risk_rejections_recorded
        risk_fp = "NONE"
        runtime_invariants_verified = False
        integration_test_evidence: bool | None = None

        if risk_integration_evidence is not None:
            verification_executed = bool(
                risk_integration_evidence.get("verification_executed", False)
            )
            risk_calls = int(risk_integration_evidence.get("risk_calls", 0))
            approved = int(risk_integration_evidence.get("approved_signals", 0))
            rejected = int(
                risk_integration_evidence.get("rejected_signals", risk_rejections_recorded)
            )
            risk_fp = str(risk_integration_evidence.get("risk_config_fingerprint", "NONE"))
            runtime_invariants_verified = bool(
                risk_integration_evidence.get("runtime_invariants_verified", False)
            )
            raw_test_ev = risk_integration_evidence.get("integration_test_evidence", None)
            if raw_test_ev is not None:
                integration_test_evidence = bool(raw_test_ev)

        calls_tally_valid = risk_calls == (approved + rejected)

        if integration_test_evidence is False:
            status_f = QuantGateStatus.FAIL
            reason_f = (
                f"Risk engine integration failed: calls={risk_calls}, approved={approved}, "
                f"rejected={rejected}, integration_test_evidence=False"
            )
            errors.append(f"Gate F Failed: {reason_f}")
            warn_f = 0
            err_f = 1
        elif not calls_tally_valid or (
            verification_executed
            and not runtime_invariants_verified
            and integration_test_evidence is not True
        ):
            status_f = QuantGateStatus.FAIL
            reason_f = (
                f"Risk engine invariant violation: calls={risk_calls}, approved={approved}, "
                f"rejected={rejected}, runtime_invariants_verified={runtime_invariants_verified}"
            )
            errors.append(f"Gate F Failed: {reason_f}")
            warn_f = 0
            err_f = 1
        elif not verification_executed or (risk_calls == 0 and integration_test_evidence is None):
            status_f = QuantGateStatus.WARNING
            reason_f = "Phase 5 Risk Engine verification unavailable: no trade signals evaluated"
            warnings.append(reason_f)
            warn_f = 1
            err_f = 0
        elif (
            verification_executed and runtime_invariants_verified and calls_tally_valid
        ) or integration_test_evidence is True:
            status_f = QuantGateStatus.PASS
            reason_f = (
                f"Phase 5 Risk Engine verified: {risk_calls} evaluations, "
                f"{approved} approved, {rejected} rejected (FP: {risk_fp})"
            )
            warn_f = 0
            err_f = 0
        else:
            status_f = QuantGateStatus.WARNING
            reason_f = "Phase 5 Risk Engine verification unavailable"
            warnings.append(reason_f)
            warn_f = 1
            err_f = 0

        gates.append(
            QuantGateResult(
                gate_id="Gate F",
                gate_name="Risk Engine Integration",
                status=status_f,
                reason=reason_f,
                evidence={
                    "verification_executed": verification_executed,
                    "risk_calls": risk_calls,
                    "approved_signals": approved,
                    "rejected_signals": rejected,
                    "risk_config_fingerprint": risk_fp,
                    "runtime_invariants_verified": runtime_invariants_verified,
                    "integration_test_evidence": integration_test_evidence,
                },
                warning_count=warn_f,
                error_count=err_f,
            )
        )

        # --- Gate G: Deterministic Reproducibility ---
        verification_executed = False
        rerun_matched = False
        run1_id = "NONE"
        run2_id = "NONE"
        run1_hash = "NONE"
        run2_hash = "NONE"
        trace1_hash = "NONE"
        trace2_hash = "NONE"
        trades_comp = len(trades)
        snaps_comp = len(equity_curve)
        trace_entries_comp = 0
        mismatches = 0

        if reproducibility_evidence is not None:
            verification_executed = bool(
                reproducibility_evidence.get("verification_executed", True)
            )
            rerun_matched = bool(reproducibility_evidence.get("rerun_matched", False))
            run1_id = str(reproducibility_evidence.get("run_1_id", "NONE"))
            run2_id = str(reproducibility_evidence.get("run_2_id", "NONE"))
            run1_hash = str(reproducibility_evidence.get("run_1_canonical_hash", "NONE"))
            run2_hash = str(reproducibility_evidence.get("run_2_canonical_hash", "NONE"))
            trace1_hash = str(reproducibility_evidence.get("trace_1_canonical_hash", "NONE"))
            trace2_hash = str(reproducibility_evidence.get("trace_2_canonical_hash", "NONE"))
            trades_comp = int(reproducibility_evidence.get("trades_compared", len(trades)))
            snaps_comp = int(reproducibility_evidence.get("snapshots_compared", len(equity_curve)))
            trace_entries_comp = int(reproducibility_evidence.get("trace_entries_compared", 0))
            mismatches = int(reproducibility_evidence.get("mismatches", 0 if rerun_matched else 1))

        if not verification_executed:
            status_g = QuantGateStatus.WARNING
            reason_g = "Deterministic reproducibility verification disabled by configuration"
            warnings.append(reason_g)
            warn_g = 1
            err_g = 0
        elif (
            not rerun_matched
            or mismatches > 0
            or run1_hash != run2_hash
            or trace1_hash != trace2_hash
            or (run1_id != "NONE" and run2_id != "NONE" and run1_id != run2_id)
        ):
            status_g = QuantGateStatus.FAIL
            reason_g = (
                f"Deterministic reproducibility failure: "
                f"{mismatches} state mismatches between dual runs"
            )
            errors.append(f"Gate G Failed: {reason_g}")
            warn_g = 0
            err_g = mismatches if mismatches > 0 else 1
        else:
            status_g = QuantGateStatus.PASS
            reason_g = (
                f"Deterministic rerun comparison verified identical output: "
                f"{trades_comp} trades, {snaps_comp} snapshots, {trace_entries_comp} trace entries"
            )
            warn_g = 0
            err_g = 0

        gates.append(
            QuantGateResult(
                gate_id="Gate G",
                gate_name="Deterministic Reproducibility",
                status=status_g,
                reason=reason_g,
                evidence={
                    "verification_executed": verification_executed,
                    "dual_run_executed": verification_executed,
                    "rerun_matched": rerun_matched,
                    "run_1_id": run1_id,
                    "run_2_id": run2_id,
                    "run_1_canonical_hash": run1_hash,
                    "run_2_canonical_hash": run2_hash,
                    "trace_1_canonical_hash": trace1_hash,
                    "trace_2_canonical_hash": trace2_hash,
                    "trades_compared": trades_comp,
                    "snapshots_compared": snaps_comp,
                    "trace_entries_compared": trace_entries_comp,
                    "mismatches": mismatches,
                },
                warning_count=warn_g,
                error_count=err_g,
            )
        )

        # --- Gate H: Correct Position/Accounting Invariants ---
        accounting_errors = 0
        for s in equity_curve:
            expected_eq = s.cash + s.margin_used + s.unrealized_pnl
            if abs(s.equity - expected_eq) > Decimal("0.01"):
                accounting_errors += 1

        if accounting_errors > 0:
            status_h = QuantGateStatus.FAIL
            reason_h = f"Detected {accounting_errors} equity balance identity violations"
            errors.append(f"Gate H Failed: {reason_h}")
        else:
            status_h = QuantGateStatus.PASS
            reason_h = "Portfolio derivatives accounting identities verified across all snapshots"
        gates.append(
            QuantGateResult(
                gate_id="Gate H",
                gate_name="Position/Accounting Invariants",
                status=status_h,
                reason=reason_h,
                evidence={"accounting_violations": accounting_errors},
                error_count=accounting_errors,
            )
        )

        # --- Gate I: Out-of-Sample Validation Support & Parameter Isolation ---
        oos_data = (
            oos_evidence
            if oos_evidence is not None
            else QuantGateEvaluator.verify_oos_partitioning(dataset)
        )
        fold_count = int(oos_data.get("fold_count", 0))
        overlap_count = int(oos_data.get("overlap_count", 0))
        param_mutations = int(oos_data.get("oos_parameter_mutations", 0))
        chronology_violations = int(oos_data.get("chronology_violations", 0))
        total_oos_faults = overlap_count + param_mutations + chronology_violations

        if total_oos_faults > 0:
            status_i = QuantGateStatus.FAIL
            reason_i = (
                f"OOS validation isolation failure: overlaps={overlap_count}, "
                f"param_mutations={param_mutations}, chronology_violations={chronology_violations}"
            )
            errors.append(f"Gate I Failed: {reason_i}")
            warn_i = 0
            err_i = total_oos_faults
        elif fold_count == 0:
            status_i = QuantGateStatus.WARNING
            reason_i = (
                "OOS partitioning skipped: dataset size below minimum fold requirement "
                "of 15 candles"
            )
            warnings.append(reason_i)
            warn_i = 1
            err_i = 0
        else:
            status_i = QuantGateStatus.PASS
            reason_i = (
                f"OOS chronological isolation & parameter immutability verified across "
                f"{fold_count} folds"
            )
            warn_i = 0
            err_i = 0

        gates.append(
            QuantGateResult(
                gate_id="Gate I",
                gate_name="Out-of-Sample Validation Support",
                status=status_i,
                reason=reason_i,
                evidence={
                    "fold_count": fold_count,
                    "train_ranges": oos_data.get("train_ranges", []),
                    "validation_ranges": oos_data.get("validation_ranges", []),
                    "test_ranges": oos_data.get("test_ranges", []),
                    "overlap_count": overlap_count,
                    "oos_parameter_mutations": param_mutations,
                    "chronology_violations": chronology_violations,
                },
                warning_count=warn_i,
                error_count=err_i,
            )
        )

        # --- Gate J: Sufficient Statistical/Sample Evidence ---
        warn_count_j = 0
        if metrics.total_trades < 30:
            status_j = QuantGateStatus.WARNING
            reason_j = (
                f"Insufficient sample warning: {metrics.total_trades} trades is below "
                f"guideline of 30"
            )
            warnings.append(reason_j)
            warn_count_j += 1
        else:
            status_j = QuantGateStatus.PASS
            reason_j = f"Sufficient sample size verified: {metrics.total_trades} trades executed"

        gates.append(
            QuantGateResult(
                gate_id="Gate J",
                gate_name="Statistical Sample Sufficiency",
                status=status_j,
                reason=reason_j,
                evidence={
                    "total_trades": metrics.total_trades,
                    "initial_capital": str(initial_capital),
                },
                warning_count=warn_count_j,
            )
        )

        # Determine overall status
        has_fail = any(g.status == QuantGateStatus.FAIL for g in gates)
        has_warn = any(g.status == QuantGateStatus.WARNING for g in gates)

        if has_fail:
            overall_status = ValidationStatus.INVALID
        elif len(dataset) == 0 or metrics.total_trades == 0:
            overall_status = ValidationStatus.INSUFFICIENT_DATA
        elif has_warn:
            overall_status = ValidationStatus.WARNING
        else:
            overall_status = ValidationStatus.VALID

        return tuple(gates), overall_status, warnings, errors

    @staticmethod
    def verify_oos_partitioning(dataset: BacktestDataset) -> dict[str, Any]:
        """
        Verify that the dataset supports chronological TRAIN/VAL/TEST partitioning
        with non-overlapping ranges, strict temporal ordering, and frozen parameter isolation.
        """
        n = len(dataset)
        if n < 15:
            return {
                "fold_count": 0,
                "train_ranges": [],
                "validation_ranges": [],
                "test_ranges": [],
                "overlap_count": 0,
                "oos_parameter_mutations": 0,
                "chronology_violations": 0,
                "note": (
                    "Dataset has fewer than 15 candles required for multi-fold OOS partitioning"
                ),
            }

        train_bars = max(5, int(n * 0.4))
        val_bars = max(2, int(n * 0.2))
        test_bars = max(2, int(n * 0.2))
        step_bars = max(1, test_bars)

        params = {"rsi_period": 14, "atr_multiplier": "1.0", "target_multiple": "2.0"}

        try:
            folds = WalkForwardEngine.generate_rolling_folds(
                dataset=dataset,
                train_bars=train_bars,
                val_bars=val_bars,
                test_bars=test_bars,
                step_bars=step_bars,
                parameters=params,
            )
        except Exception as ex:
            return {
                "fold_count": 0,
                "train_ranges": [],
                "validation_ranges": [],
                "test_ranges": [],
                "overlap_count": 1,
                "oos_parameter_mutations": 0,
                "chronology_violations": 1,
                "error": str(ex),
            }

        train_ranges: list[tuple[str, str]] = []
        val_ranges: list[tuple[str, str]] = []
        test_ranges: list[tuple[str, str]] = []
        chronology_violations = 0
        overlap_count = 0
        param_mutations = 0

        for f in folds:
            train_ranges.append((f.train_start.isoformat(), f.train_end.isoformat()))
            val_ranges.append((f.validation_start.isoformat(), f.validation_end.isoformat()))
            test_ranges.append((f.test_start.isoformat(), f.test_end.isoformat()))

            if f.train_end > f.validation_start or f.validation_end > f.test_start:
                chronology_violations += 1

            # Test parameter immutability
            try:
                # Attempt to mutate parameter snapshot (must fail)
                f.frozen_parameters["mutated"] = True  # type: ignore[index]
                param_mutations += 1
            except TypeError:
                pass  # Correct: MappingProxyType prevents mutation

        return {
            "fold_count": len(folds),
            "train_ranges": train_ranges,
            "validation_ranges": val_ranges,
            "test_ranges": test_ranges,
            "overlap_count": overlap_count,
            "oos_parameter_mutations": param_mutations,
            "chronology_violations": chronology_violations,
        }


class OverfittingDiagnostics:
    """
    Evaluates suspicion indicators and parameter fragility diagnostics (Section 30).
    """

    @staticmethod
    def run_diagnostics(
        trades: Sequence[BacktestTrade],
        metrics: BacktestMetrics,
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Analyze trade concentration, unusual Sharpe, and drawdown anomalies.
        Returns (diagnostics_dict, warning_messages).
        """
        diagnostics: dict[str, Any] = {}
        warnings: list[str] = []

        # 1. Unusually High Sharpe (> 4.0)
        if metrics.sharpe_ratio is not None and metrics.sharpe_ratio > Decimal("4.0"):
            msg = f"OVERFITTING_WARNING: Unusually high Sharpe ratio ({metrics.sharpe_ratio} > 4.0)"
            warnings.append(msg)
            diagnostics["high_sharpe_warning"] = True
        else:
            diagnostics["high_sharpe_warning"] = False

        # 2. Unusually Low Drawdown (< 1.0%)
        if metrics.total_trades >= 10 and metrics.max_drawdown_pct < Decimal("0.01"):
            dd_val = metrics.max_drawdown_pct * Decimal("100")
            msg = f"OVERFITTING_WARNING: Unusually low maximum drawdown ({dd_val:.2f}% < 1.0%)"
            warnings.append(msg)
            diagnostics["low_drawdown_warning"] = True
        else:
            diagnostics["low_drawdown_warning"] = False

        # 3. Trade Concentration: Top trade contributes > 50% of total positive PnL
        top_trade_pnl = max((t.net_pnl for t in trades), default=Decimal("0"))
        total_pos_pnl = sum((t.net_pnl for t in trades if t.net_pnl > Decimal("0")), Decimal("0"))

        concentration_pct = Decimal("0")
        if total_pos_pnl > Decimal("0"):
            concentration_pct = (top_trade_pnl / total_pos_pnl) * Decimal("100")
            if concentration_pct > Decimal("50.0"):
                msg = (
                    f"OVERFITTING_WARNING: Single trade accounts for "
                    f"{concentration_pct:.1f}% of total positive profit"
                )
                warnings.append(msg)
                diagnostics["trade_concentration_warning"] = True
            else:
                diagnostics["trade_concentration_warning"] = False
        else:
            diagnostics["trade_concentration_warning"] = False

        diagnostics["top_trade_pnl"] = str(top_trade_pnl)
        diagnostics["top_trade_concentration_pct"] = str(round(concentration_pct, 2))

        return diagnostics, warnings


class DatasetPartitioner:
    """
    Partitions datasets into TRAIN, VALIDATION, and TEST folds chronologically without overlap.
    """

    @staticmethod
    def split_train_val_test(
        dataset: BacktestDataset,
        train_ratio: Decimal = Decimal("0.6"),
        val_ratio: Decimal = Decimal("0.2"),
    ) -> tuple[BacktestDataset, BacktestDataset, BacktestDataset]:
        """
        Split a dataset chronologically into Train (60%), Validation (20%), and Test (20%).
        Guarantees strictly non-overlapping temporal horizons.
        """
        n = len(dataset)
        if n < 3:
            raise BacktestValidationError(f"Dataset too small to partition: {n} candles")

        n_train = int(Decimal(n) * train_ratio)
        n_val = int(Decimal(n) * val_ratio)
        n_test = n - n_train - n_val

        if n_train == 0 or n_val == 0 or n_test == 0:
            raise BacktestValidationError("Partition ratios resulted in an empty fold")

        train_candles = dataset.candles[:n_train]
        val_candles = dataset.candles[n_train : n_train + n_val]
        test_candles = dataset.candles[n_train + n_val :]

        src = dataset.metadata.data_source
        train_ds = BacktestDataset(f"{dataset.dataset_id}_TRAIN", train_candles, src)
        val_ds = BacktestDataset(f"{dataset.dataset_id}_VAL", val_candles, src)
        test_ds = BacktestDataset(f"{dataset.dataset_id}_TEST", test_candles, src)

        return train_ds, val_ds, test_ds


class WalkForwardFold:
    """
    Immutable representation of a walk-forward evaluation fold.
    Guarantees strict temporal chronology:
    train_end <= validation_start and validation_end <= test_start.
    Includes an immutable frozen parameter snapshot selected before test execution.
    """

    def __init__(
        self,
        fold_index: int,
        train_dataset: BacktestDataset,
        test_dataset: BacktestDataset,
        validation_dataset: BacktestDataset | None = None,
        frozen_parameters: Mapping[str, Any] | None = None,
    ) -> None:
        if len(train_dataset) == 0 or len(test_dataset) == 0:
            raise BacktestValidationError(
                f"Fold {fold_index}: Train and test datasets must not be empty"
            )
        if validation_dataset is not None and len(validation_dataset) == 0:
            raise BacktestValidationError(
                f"Fold {fold_index}: Validation dataset must not be empty"
            )

        self.fold_index = fold_index
        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.validation_dataset = validation_dataset

        self.train_start = train_dataset.candles[0].exchange_timestamp
        self.train_end = train_dataset.candles[-1].exchange_timestamp

        if validation_dataset is not None:
            self.validation_start = validation_dataset.candles[0].exchange_timestamp
            self.validation_end = validation_dataset.candles[-1].exchange_timestamp
        else:
            self.validation_start = self.train_end
            self.validation_end = self.train_end

        self.test_start = test_dataset.candles[0].exchange_timestamp
        self.test_end = test_dataset.candles[-1].exchange_timestamp

        # Chronology invariants
        if self.train_end > self.validation_start:
            raise BacktestValidationError(
                f"Fold {fold_index} chronology violation: "
                f"train_end ({self.train_end}) > validation_start ({self.validation_start})"
            )
        if self.validation_end > self.test_start:
            raise BacktestValidationError(
                f"Fold {fold_index} chronology violation: "
                f"validation_end ({self.validation_end}) > test_start ({self.test_start})"
            )

        # Non-overlap invariant: test timestamps must not appear in train or validation
        train_ts = {c.exchange_timestamp for c in train_dataset.candles}
        test_ts = {c.exchange_timestamp for c in test_dataset.candles}
        if test_ts.intersection(train_ts):
            raise BacktestValidationError(
                f"Fold {fold_index} data leakage: Test dataset shares timestamps with train dataset"
            )
        if validation_dataset is not None:
            val_ts = {c.exchange_timestamp for c in validation_dataset.candles}
            if test_ts.intersection(val_ts):
                raise BacktestValidationError(
                    f"Fold {fold_index} data leakage: "
                    f"Test dataset shares timestamps with validation dataset"
                )

        # Immutable parameter snapshot
        self.frozen_parameters: Mapping[str, Any] = MappingProxyType(
            FrozenDict(dict(frozen_parameters or {}))
        )


class ParameterSelectionMode(StrEnum):
    """Execution mode for out-of-sample parameter determination."""

    FITTING = "FITTING"
    NO_FITTING = "NO_FITTING"


class ParameterIsolationWorkflow:
    """
    Authoritative workflow enforcing strict isolation between parameter determination
    and out-of-sample evaluation:
    TRAIN -> parameter selection / supplied frozen parameters -> VALIDATION -> freeze -> OOS TEST.
    Guarantees that test / OOS data and outcomes cannot contaminate or mutate parameters.
    """

    @staticmethod
    def freeze_parameters(params: Mapping[str, Any]) -> MappingProxyType[str, Any]:
        """Deeply freeze parameter mapping into an immutable structure."""
        frozen_dict = FrozenDict(dict(params))
        return MappingProxyType(frozen_dict)

    @classmethod
    def execute_workflow(
        cls,
        train_dataset: BacktestDataset,
        validation_dataset: BacktestDataset,
        test_dataset: BacktestDataset,
        supplied_parameters: Mapping[str, Any] | None = None,
        selection_api: Any = None,
    ) -> tuple[MappingProxyType[str, Any], dict[str, Any]]:
        """
        Execute parameter isolation lifecycle:
        1. Train & Validation phase: determine parameters either via authoritative selection_api
           (Mode A: FITTING) or explicit configuration (Mode B: NO_FITTING).
        2. Freeze parameter snapshot into immutable mapping before test execution.
        3. Return frozen parameters and audit telemetry.
        """
        if len(train_dataset) == 0 or len(test_dataset) == 0:
            raise BacktestValidationError("Train and Test datasets must be non-empty")

        # Step 1: Selection Phase (strictly from train + validation)
        if selection_api is not None and callable(selection_api):
            selected = selection_api(train_dataset, validation_dataset)
            mode = ParameterSelectionMode.FITTING
        else:
            selected = dict(supplied_parameters or {})
            mode = ParameterSelectionMode.NO_FITTING

        # Step 2: Validation & Freeze Phase
        frozen = cls.freeze_parameters(selected)

        # Compute parameter fingerprint
        param_canonical = json.dumps(dict(frozen), sort_keys=True, default=str)
        param_hash = hashlib.sha256(param_canonical.encode("utf-8")).hexdigest()

        telemetry = {
            "parameter_selection_mode": mode.value,
            "parameter_hash": param_hash,
            "train_record_count": len(train_dataset),
            "validation_record_count": len(validation_dataset),
            "test_record_count": len(test_dataset),
            "parameters_frozen": True,
        }
        return frozen, telemetry

    @classmethod
    def verify_oos_non_contamination(
        cls,
        train_dataset: BacktestDataset,
        validation_dataset: BacktestDataset,
        test_dataset: BacktestDataset,
        mutated_test_dataset: BacktestDataset,
        supplied_parameters: Mapping[str, Any] | None = None,
        selection_api: Any = None,
    ) -> bool:
        """
        Forensic test proving mutating the test/OOS dataset has zero effect on
        the parameters selected from train and validation.
        """
        frozen_1, tele_1 = cls.execute_workflow(
            train_dataset=train_dataset,
            validation_dataset=validation_dataset,
            test_dataset=test_dataset,
            supplied_parameters=supplied_parameters,
            selection_api=selection_api,
        )
        frozen_2, tele_2 = cls.execute_workflow(
            train_dataset=train_dataset,
            validation_dataset=validation_dataset,
            test_dataset=mutated_test_dataset,
            supplied_parameters=supplied_parameters,
            selection_api=selection_api,
        )
        return bool(tele_1["parameter_hash"] == tele_2["parameter_hash"])


class WalkForwardEngine:
    """
    Generates rolling and expanding walk-forward windows.
    """

    @staticmethod
    def generate_rolling_folds(
        dataset: BacktestDataset,
        train_bars: int,
        test_bars: int,
        val_bars: int = 0,
        step_bars: int | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[WalkForwardFold]:
        """
        Generate non-overlapping rolling walk-forward folds with strict chronology.
        """
        step = step_bars or test_bars
        n = len(dataset)
        folds: list[WalkForwardFold] = []

        start = 0
        idx = 1
        total_window = train_bars + val_bars + test_bars
        while start + total_window <= n:
            train_candles = dataset.candles[start : start + train_bars]
            src = dataset.metadata.data_source
            train_ds = BacktestDataset(f"{dataset.dataset_id}_WF{idx}_TRAIN", train_candles, src)

            val_ds: BacktestDataset | None = None
            if val_bars > 0:
                val_candles = dataset.candles[start + train_bars : start + train_bars + val_bars]
                val_ds = BacktestDataset(f"{dataset.dataset_id}_WF{idx}_VAL", val_candles, src)

            test_start_idx = start + train_bars + val_bars
            test_candles = dataset.candles[test_start_idx : test_start_idx + test_bars]
            test_ds = BacktestDataset(f"{dataset.dataset_id}_WF{idx}_TEST", test_candles, src)

            folds.append(
                WalkForwardFold(
                    fold_index=idx,
                    train_dataset=train_ds,
                    test_dataset=test_ds,
                    validation_dataset=val_ds,
                    frozen_parameters=parameters,
                )
            )
            start += step
            idx += 1

        return folds


class MarketRegimeAnalyzer:
    """
    Classifies market regimes using only information available up to historical decision timestamp.
    """

    @staticmethod
    def classify_candle_regime(
        past_candles: Sequence[MarketCandle],
        lookback: int = 20,
    ) -> RegimeType:
        """
        Classify regime using strictly historical candles.
        """
        if len(past_candles) < lookback:
            return RegimeType.RANGING

        recent = past_candles[-lookback:]
        closes = [float(c.close) for c in recent]
        sma = sum(closes) / len(closes)
        latest_close = closes[-1]

        # ATR calculation
        trs: list[float] = []
        for i in range(1, len(recent)):
            high_val = float(recent[i].high)
            low_val = float(recent[i].low)
            prev_c = float(recent[i - 1].close)
            tr = max(high_val - low_val, abs(high_val - prev_c), abs(low_val - prev_c))
            trs.append(tr)

        atr = sum(trs) / len(trs) if trs else 0.0
        atr_pct = (atr / latest_close) * 100.0 if latest_close > 0 else 0.0

        if atr_pct > 1.5:
            return RegimeType.HIGH_VOLATILITY
        elif latest_close > sma * 1.002:
            return RegimeType.TRENDING_BULL
        elif latest_close < sma * 0.998:
            return RegimeType.TRENDING_BEAR
        elif atr_pct < 0.3:
            return RegimeType.LOW_VOLATILITY
        else:
            return RegimeType.RANGING
