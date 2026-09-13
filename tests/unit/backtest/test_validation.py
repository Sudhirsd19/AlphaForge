"""
Unit tests for alphaforge.backtest.validation.
Verifies Quant Gates A-J audits, overfitting diagnostics, OOS partitioning,
walk-forward generation, and market regime analysis.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from alphaforge.backtest.datasets import BacktestDataset
from alphaforge.backtest.engine import BacktestEngine
from alphaforge.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestTrade,
    EquitySnapshot,
    QuantGateStatus,
    RegimeType,
    ValidationStatus,
)
from alphaforge.backtest.validation import (
    DatasetPartitioner,
    MarketRegimeAnalyzer,
    ParameterIsolationWorkflow,
    ParameterSelectionMode,
    QuantGateEvaluator,
    WalkForwardEngine,
)
from alphaforge.contract.models import ContractMaster
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.risk.enums import TradeSide


def _create_candle(ts: datetime, close_p: Decimal) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(milliseconds=10),
        timeframe="3m",
        open=close_p,
        high=close_p + Decimal("10"),
        low=close_p - Decimal("10"),
        close=close_p,
        volume=100,
        source="NSE",
    )


def test_quant_gate_evaluator_all_pass() -> None:
    """Verify all gates pass on valid backtest execution."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset(
        "DS",
        [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(50)],
    )

    trades = [
        BacktestTrade(
            trade_id=f"T{i}",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_timestamp=t0 + timedelta(hours=i),
            entry_price=Decimal("24000"),
            entry_quantity=50,
            exit_timestamp=t0 + timedelta(hours=i, minutes=30),
            exit_price=Decimal("24050"),
            exit_quantity=50,
            gross_pnl=Decimal("2500"),
            fees=Decimal("20"),
            slippage=Decimal("10"),
            net_pnl=Decimal("2470"),
            return_pct=Decimal("0.2"),
            holding_duration_seconds=1800,
            max_favorable_excursion=Decimal("50"),
            max_adverse_excursion=Decimal("5"),
            entry_signal_id=f"SIG-{i}",
            strategy_version="1.0.0",
            exit_reason="TARGET",
        )
        for i in range(35)  # >= 30 trades for Gate J!
    ]

    equity_curve = [
        EquitySnapshot(
            timestamp=t0 + timedelta(hours=i),
            equity=Decimal("1000000") + Decimal(i * 100),
            cash=Decimal("1000000") + Decimal(i * 100),
            realized_pnl=Decimal(i * 100),
            unrealized_pnl=Decimal("0"),
            cumulative_fees=Decimal("0"),
            cumulative_slippage=Decimal("0"),
            notional_exposure=Decimal("0"),
            drawdown=Decimal("0"),
            drawdown_pct=Decimal("0"),
        )
        for i in range(35)
    ]

    metrics = BacktestMetrics(
        total_return=Decimal("86450"),
        total_return_pct=Decimal("8.64"),
        total_trades=35,
        winning_trades=35,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("1"),
        loss_rate=Decimal("0"),
        average_win=Decimal("2470"),
        average_loss=Decimal("0"),
        expectancy=Decimal("2470"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("87500"),
        total_fees=Decimal("700"),
        total_slippage=Decimal("350"),
        net_pnl=Decimal("86450"),
    )

    look_ahead_evidence = {
        "verification_executed": True,
        "future_mutation_test_passed": True,
        "historical_trace_comparison_passed": True,
        "future_append_invariance_passed": True,
        "future_mutation_tests": 1,
        "historical_decisions_compared": 10,
        "historical_orders_compared": 10,
        "historical_fills_compared": 10,
        "historical_equity_snapshots_compared": 10,
        "causality_violations": 0,
        "temporal_inversions": 0,
    }
    contract_evidence = {
        "verification_executed": True,
        "contract_master_present": True,
        "contract_id": "NIFTY-SPOT",
        "contract_checks": 10,
        "expiry_checks": 10,
        "active_contract_checks": 10,
        "lot_size_checks": 10,
        "multiplier_checks": 10,
        "expired_trade_attempts": 0,
        "post_expiry_fills": 0,
        "invalid_contracts": 0,
    }
    reproducibility_evidence = {
        "verification_executed": True,
        "dual_run_executed": True,
        "rerun_matched": True,
        "run_1_canonical_hash": "HASH1",
        "run_2_canonical_hash": "HASH1",
        "trace_1_canonical_hash": "TRACE1",
        "trace_2_canonical_hash": "TRACE1",
        "trades_compared": 35,
        "snapshots_compared": 35,
        "trace_entries_compared": 100,
        "mismatches": 0,
    }
    risk_integration_evidence = {
        "verification_executed": True,
        "risk_calls": 35,
        "approved_signals": 35,
        "rejected_signals": 0,
        "risk_config_fingerprint": "FP_RISK_1234",
        "runtime_invariants_verified": True,
        "integration_test_evidence": None,
    }

    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        look_ahead_evidence=look_ahead_evidence,
        contract_evidence=contract_evidence,
        risk_integration_evidence=risk_integration_evidence,
        reproducibility_evidence=reproducibility_evidence,
    )

    assert status == ValidationStatus.VALID
    assert len(errs) == 0
    assert all(g.status == QuantGateStatus.PASS for g in gates)


def test_quant_gate_evaluator_unverified_gates_yield_warning() -> None:
    """Verify that omitting verification evidence yields WARNING, never silent PASS."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset(
        "DS",
        [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(50)],
    )
    trades = [
        BacktestTrade(
            trade_id=f"T{i}",
            symbol="NIFTY",
            side=TradeSide.LONG,
            entry_timestamp=t0 + timedelta(hours=i),
            entry_price=Decimal("24000"),
            entry_quantity=50,
            exit_timestamp=t0 + timedelta(hours=i, minutes=30),
            exit_price=Decimal("24050"),
            exit_quantity=50,
            gross_pnl=Decimal("2500"),
            fees=Decimal("20"),
            slippage=Decimal("10"),
            net_pnl=Decimal("2470"),
            return_pct=Decimal("0.2"),
            holding_duration_seconds=1800,
            max_favorable_excursion=Decimal("50"),
            max_adverse_excursion=Decimal("5"),
            entry_signal_id=f"SIG-{i}",
            strategy_version="1.0.0",
            exit_reason="TARGET",
        )
        for i in range(35)
    ]
    metrics = BacktestMetrics(
        total_return=Decimal("86450"),
        total_return_pct=Decimal("8.64"),
        total_trades=35,
        winning_trades=35,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("1"),
        loss_rate=Decimal("0"),
        average_win=Decimal("2470"),
        average_loss=Decimal("0"),
        expectancy=Decimal("2470"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("87500"),
        total_fees=Decimal("700"),
        total_slippage=Decimal("350"),
        net_pnl=Decimal("86450"),
    )

    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=trades,
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
    )

    assert status == ValidationStatus.WARNING
    gate_a = next(g for g in gates if g.gate_id == "Gate A")
    gate_e = next(g for g in gates if g.gate_id == "Gate E")
    gate_f = next(g for g in gates if g.gate_id == "Gate F")
    gate_g = next(g for g in gates if g.gate_id == "Gate G")
    assert gate_a.status == QuantGateStatus.WARNING
    assert gate_e.status == QuantGateStatus.WARNING
    assert gate_f.status == QuantGateStatus.WARNING
    assert gate_g.status == QuantGateStatus.WARNING


def test_quant_gate_j_insufficient_sample_warning() -> None:
    """Verify Gate J issues warning when trade count < 30."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])

    metrics = BacktestMetrics(
        total_return=Decimal("100"),
        total_return_pct=Decimal("0.01"),
        total_trades=5,  # < 30
        winning_trades=3,
        losing_trades=2,
        break_even_trades=0,
        win_rate=Decimal("0.6"),
        loss_rate=Decimal("0.4"),
        average_win=Decimal("100"),
        average_loss=Decimal("100"),
        expectancy=Decimal("20"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("100"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("100"),
    )

    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
    )
    # Trade count < 30 triggers WARNING on Gate J
    gate_j = next(g for g in gates if g.gate_id == "Gate J")
    assert gate_j.status == QuantGateStatus.WARNING
    assert status == ValidationStatus.WARNING


def test_dataset_partitioner_non_overlapping() -> None:
    """Verify train/val/test splits have non-overlapping bounds."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(100)]
    dataset = BacktestDataset("DS_100", candles)

    train, val, test = DatasetPartitioner.split_train_val_test(dataset)
    assert len(train) == 60
    assert len(val) == 20
    assert len(test) == 20

    # Ensure no temporal overlap
    assert train.candles[-1].exchange_timestamp < val.candles[0].exchange_timestamp
    assert val.candles[-1].exchange_timestamp < test.candles[0].exchange_timestamp


def test_walk_forward_engine() -> None:
    """Verify rolling walk-forward fold generation."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(100)]
    dataset = BacktestDataset("DS_100", candles)

    folds = WalkForwardEngine.generate_rolling_folds(
        dataset, train_bars=40, test_bars=10, step_bars=10
    )
    assert len(folds) == 6
    for f in folds:
        assert len(f.train_dataset) == 40
        assert len(f.test_dataset) == 10
        train_last_ts = f.train_dataset.candles[-1].exchange_timestamp
        test_first_ts = f.test_dataset.candles[0].exchange_timestamp
        assert train_last_ts < test_first_ts


def test_market_regime_analyzer() -> None:
    """Verify regime classification based only on past candles."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    # Trending upwards
    bull_candles = [
        _create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000") + Decimal(i * 10))
        for i in range(30)
    ]
    regime = MarketRegimeAnalyzer.classify_candle_regime(bull_candles)
    assert regime in (RegimeType.TRENDING_BULL, RegimeType.HIGH_VOLATILITY)


def test_quant_gate_a_look_ahead_violation_failure() -> None:
    """
    Verify Gate A fails when future mutation discrepancies or temporal inversions are detected.
    """
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )

    # Causality violation evidence provided
    evidence = {
        "future_mutation_tests": 1,
        "causality_violations": 2,
        "historical_decisions_compared": 5,
        "historical_orders_compared": 5,
        "historical_fills_compared": 5,
        "historical_equity_snapshots_compared": 10,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        look_ahead_evidence=evidence,
    )
    gate_a = next(g for g in gates if g.gate_id == "Gate A")
    assert gate_a.status == QuantGateStatus.FAIL
    assert gate_a.error_count == 2
    assert status == ValidationStatus.INVALID
    assert any("Gate A Failed" in e for e in errs)


def test_quant_gate_f_risk_integration_failure() -> None:
    """Verify Gate F fails when risk integration assertions fail or counts mismatch."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )

    # Risk calls mismatch: calls=5, but approved=2, rejected=1 (missing 2 evaluations)
    evidence = {
        "verification_executed": True,
        "risk_calls": 5,
        "approved_signals": 2,
        "rejected_signals": 1,
        "risk_config_fingerprint": "TEST_FP",
        "runtime_invariants_verified": False,
        "integration_test_evidence": False,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        risk_integration_evidence=evidence,
    )
    gate_f = next(g for g in gates if g.gate_id == "Gate F")
    assert gate_f.status == QuantGateStatus.FAIL
    assert status == ValidationStatus.INVALID
    assert any("Gate F Failed" in e for e in errs)


def test_quant_gate_f_runtime_provenance_pass() -> None:
    """Verify Gate F passes with genuine deterministic runtime provenance."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("100"),
        total_return_pct=Decimal("0.01"),
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("1"),
        loss_rate=Decimal("0"),
        average_win=Decimal("100"),
        average_loss=Decimal("0"),
        expectancy=Decimal("100"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("100"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("100"),
    )
    evidence = {
        "verification_executed": True,
        "risk_calls": 10,
        "approved_signals": 8,
        "rejected_signals": 2,
        "risk_config_fingerprint": "PROVENANCE_FP_123",
        "runtime_invariants_verified": True,
        "integration_test_evidence": None,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        risk_integration_evidence=evidence,
    )
    gate_f = next(g for g in gates if g.gate_id == "Gate F")
    assert gate_f.status == QuantGateStatus.PASS
    assert gate_f.evidence["verification_executed"] is True
    assert gate_f.evidence["risk_calls"] == 10
    assert gate_f.evidence["approved_signals"] == 8
    assert gate_f.evidence["rejected_signals"] == 2
    assert gate_f.evidence["runtime_invariants_verified"] is True
    assert "PROVENANCE_FP_123" in gate_f.reason


def test_quant_gate_f_unexecuted_warning() -> None:
    """Verify Gate F yields WARNING when verification was unavailable (no calls)."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )
    evidence = {
        "verification_executed": False,
        "risk_calls": 0,
        "approved_signals": 0,
        "rejected_signals": 0,
        "risk_config_fingerprint": "ZERO_CALLS_FP",
        "runtime_invariants_verified": False,
        "integration_test_evidence": None,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        risk_integration_evidence=evidence,
    )
    gate_f = next(g for g in gates if g.gate_id == "Gate F")
    assert gate_f.status == QuantGateStatus.WARNING
    assert any("Gate F" in w or "Risk Engine" in w for w in warns)


def test_quant_gate_g_reproducibility_mismatch_failure() -> None:
    """Verify Gate G fails when canonical rerun hashes mismatch."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )

    # Hash mismatch between run 1 and run 2
    evidence = {
        "rerun_matched": False,
        "run_1_canonical_hash": "HASH_AAA",
        "run_2_canonical_hash": "HASH_BBB",
        "trades_compared": 5,
        "snapshots_compared": 20,
        "mismatches": 1,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        reproducibility_evidence=evidence,
    )
    gate_g = next(g for g in gates if g.gate_id == "Gate G")
    assert gate_g.status == QuantGateStatus.FAIL
    assert gate_g.error_count == 1
    assert status == ValidationStatus.INVALID
    assert any("Gate G Failed" in e for e in errs)


def test_quant_gate_i_oos_chronology_and_parameter_isolation() -> None:
    """Verify Gate I evaluates OOS chronological order, non-overlap, and parameter immutability."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(50)]
    dataset = BacktestDataset("DS_50", candles)

    # 1. Automatic partitioning verification passes
    oos_result = QuantGateEvaluator.verify_oos_partitioning(dataset)
    assert oos_result["fold_count"] > 0
    assert oos_result["overlap_count"] == 0
    assert oos_result["chronology_violations"] == 0
    assert oos_result["oos_parameter_mutations"] == 0

    # 2. Test parameter immutability on WalkForwardFold
    params = {"rsi_period": 14, "target_multiple": "2.0"}
    folds = WalkForwardEngine.generate_rolling_folds(
        dataset=dataset,
        train_bars=20,
        val_bars=10,
        test_bars=10,
        step_bars=10,
        parameters=params,
    )
    assert len(folds) > 0
    f = folds[0]

    with pytest.raises(TypeError):
        f.frozen_parameters["new_key"] = 123  # type: ignore[index]


def test_parameter_isolation_workflow_modes() -> None:
    """Verify ParameterIsolationWorkflow executes in Mode A (FITTING) and Mode B (NO_FITTING)."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    train_c = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(20)]
    val_c = [
        _create_candle(t0 + timedelta(minutes=3 * (20 + i)), Decimal("24050")) for i in range(10)
    ]
    test_c = [
        _create_candle(t0 + timedelta(minutes=3 * (30 + i)), Decimal("24100")) for i in range(10)
    ]

    train_ds = BacktestDataset("TRAIN_DS", train_c)
    val_ds = BacktestDataset("VAL_DS", val_c)
    test_ds = BacktestDataset("TEST_DS", test_c)

    # Mode B: NO_FITTING with supplied parameters
    supplied = {"atr_period": 14, "risk_fraction": "0.01"}
    frozen_b, tele_b = ParameterIsolationWorkflow.execute_workflow(
        train_dataset=train_ds,
        validation_dataset=val_ds,
        test_dataset=test_ds,
        supplied_parameters=supplied,
    )
    assert tele_b["parameter_selection_mode"] == ParameterSelectionMode.NO_FITTING.value
    assert tele_b["parameters_frozen"] is True
    assert tele_b["train_record_count"] == 20
    assert tele_b["validation_record_count"] == 10
    assert tele_b["test_record_count"] == 10
    assert frozen_b["atr_period"] == 14

    # Mode A: FITTING with selection API callback
    def mock_selection_api(train: BacktestDataset, val: BacktestDataset) -> dict[str, Any]:
        return {"fitted_window": len(train) + len(val)}

    frozen_a, tele_a = ParameterIsolationWorkflow.execute_workflow(
        train_dataset=train_ds,
        validation_dataset=val_ds,
        test_dataset=test_ds,
        selection_api=mock_selection_api,
    )
    assert tele_a["parameter_selection_mode"] == ParameterSelectionMode.FITTING.value
    assert tele_a["parameters_frozen"] is True
    assert frozen_a["fitted_window"] == 30


def test_parameter_isolation_immutability() -> None:
    """Verify frozen parameter snapshots cannot be mutated."""
    params = {"alpha": 1, "beta": 2}
    frozen = ParameterIsolationWorkflow.freeze_parameters(params)

    with pytest.raises(TypeError):
        frozen["alpha"] = 99  # type: ignore[index]

    with pytest.raises(TypeError):
        frozen["gamma"] = 3  # type: ignore[index]


def test_parameter_isolation_oos_non_contamination() -> None:
    """Verify that mutating the OOS test dataset does not contaminate parameters."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    train_c = [_create_candle(t0 + timedelta(minutes=3 * i), Decimal("24000")) for i in range(20)]
    val_c = [
        _create_candle(t0 + timedelta(minutes=3 * (20 + i)), Decimal("24050")) for i in range(10)
    ]
    test_c = [
        _create_candle(t0 + timedelta(minutes=3 * (30 + i)), Decimal("24100")) for i in range(10)
    ]
    mutated_test_c = [
        _create_candle(t0 + timedelta(minutes=3 * (30 + i)), Decimal("99999")) for i in range(10)
    ]

    train_ds = BacktestDataset("TRAIN_DS", train_c)
    val_ds = BacktestDataset("VAL_DS", val_c)
    test_ds = BacktestDataset("TEST_DS", test_c)
    mutated_test_ds = BacktestDataset("MUT_TEST_DS", mutated_test_c)

    def selection_fn(train: BacktestDataset, _val: BacktestDataset) -> dict[str, Any]:
        return {"close_mean": str(train.candles[0].close)}

    non_contaminated = ParameterIsolationWorkflow.verify_oos_non_contamination(
        train_dataset=train_ds,
        validation_dataset=val_ds,
        test_dataset=test_ds,
        mutated_test_dataset=mutated_test_ds,
        selection_api=selection_fn,
    )
    assert non_contaminated is True


def test_quant_gate_e_contract_lifecycle_deliberate_failure() -> None:
    """Verify Gate E fails when post-expiry fills > 0, and warns when contract master is missing."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )

    # 1. Post-expiry fill violation causes FAIL
    evidence_fail = {
        "contract_master_present": True,
        "contract_id": "NIFTY-SPOT",
        "post_expiry_fills": 2,
        "invalid_contracts": 0,
        "contract_valid": True,
    }
    gates_fail, status_fail, warns_fail, errs_fail = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=evidence_fail,
    )
    gate_e_fail = next(g for g in gates_fail if g.gate_id == "Gate E")
    assert gate_e_fail.status == QuantGateStatus.FAIL
    assert gate_e_fail.error_count == 2
    assert any("Gate E Failed" in e for e in errs_fail)

    # 2. Missing contract master causes WARNING
    gates_warn, status_warn, warns_warn, errs_warn = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=None,
    )
    gate_e_warn = next(g for g in gates_warn if g.gate_id == "Gate E")
    assert gate_e_warn.status == QuantGateStatus.WARNING
    assert any("Contract master omitted" in w for w in warns_warn)


def test_gate_e_test_1_normal_expiry() -> None:
    """Test 1 — Normal expiry: position reaches expiry -> forced close -> Gate E PASS."""
    from tests.unit.backtest.test_engine import _load_fixture_candles

    candles = _load_fixture_candles()
    last_c = candles[-1]
    c1 = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=last_c.exchange_timestamp + timedelta(minutes=3),
        received_timestamp=last_c.exchange_timestamp + timedelta(minutes=3, milliseconds=10),
        timeframe="3m",
        open=Decimal("24100.00"),
        high=Decimal("24150.00"),
        low=Decimal("24090.00"),
        close=Decimal("24120.00"),
        volume=2000,
        source="NSE",
    )
    c2 = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=last_c.exchange_timestamp + timedelta(minutes=6),
        received_timestamp=last_c.exchange_timestamp + timedelta(minutes=6, milliseconds=10),
        timeframe="3m",
        open=Decimal("24120.00"),
        high=Decimal("24130.00"),
        low=Decimal("24080.00"),
        close=Decimal("24090.00"),
        volume=2000,
        source="NSE",
    )
    candles.extend([c1, c2])

    t0 = candles[0].exchange_timestamp
    t_exp = c2.exchange_timestamp

    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        expiry_datetime=t_exp,
        listing_datetime=t0 - timedelta(days=90),
        trading_start_datetime=t0 - timedelta(days=90),
        trading_end_datetime=t_exp,
        lot_size=1,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        data_source="NSE",
    )
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id="DS_TEST1",
        start_time=t0,
        end_time=c2.exchange_timestamp + timedelta(minutes=3),
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, BacktestDataset("DS_TEST1", candles), contract_master=contract)
    res = engine.run()

    # Position reached expiry and was closed with exit_reason CONTRACT_EXPIRED
    assert any(t.exit_reason == "CONTRACT_EXPIRED" and t.is_forced_close for t in res.trades)
    assert engine.post_expiry_fills == 0
    gate_e = next(g for g in res.quant_gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.PASS
    assert gate_e.evidence["contract_metadata_valid"] is True
    assert gate_e.evidence["lifecycle_violation"] is False
    assert gate_e.evidence["post_expiry_fills"] == 0
    assert gate_e.evidence["contract_valid"] is True


def test_gate_e_test_2_pending_entry_at_expiry() -> None:
    """Test 2 — Pending entry at expiry: rejected, post_expiry_fills == 0 -> Gate E PASS."""
    from tests.unit.backtest.test_engine import _load_fixture_candles

    candles = _load_fixture_candles()
    last_c = candles[-1]
    c1 = MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.INDEX,
        contract_id="NIFTY-SPOT",
        exchange_timestamp=last_c.exchange_timestamp + timedelta(minutes=3),
        received_timestamp=last_c.exchange_timestamp + timedelta(minutes=3, milliseconds=10),
        timeframe="3m",
        open=Decimal("24100.00"),
        high=Decimal("24150.00"),
        low=Decimal("24090.00"),
        close=Decimal("24120.00"),
        volume=2000,
        source="NSE",
    )
    candles.append(c1)

    t0 = candles[0].exchange_timestamp
    t_exp = c1.exchange_timestamp  # Expiry at c1 open when pending entry arrives

    contract = ContractMaster(
        exchange="NSE",
        segment="NFO",
        underlying_symbol="NIFTY",
        contract_id="NIFTY26SEPFUT",
        expiry_datetime=t_exp,
        listing_datetime=t0 - timedelta(days=90),
        trading_start_datetime=t0 - timedelta(days=90),
        trading_end_datetime=t_exp,
        lot_size=1,
        tick_size=Decimal("0.05"),
        contract_multiplier=Decimal("1"),
        data_source="NSE",
    )
    cfg = BacktestConfig(
        strategy_id="AF_ORB_MOMENTUM_V1",
        strategy_version="1.0.0",
        dataset_id="DS_TEST2",
        start_time=t0,
        end_time=c1.exchange_timestamp + timedelta(minutes=3),
        initial_capital=Decimal("1000000"),
        warmup_bars=15,
        verify_look_ahead=False,
        verify_reproducibility=False,
    )
    engine = BacktestEngine(cfg, BacktestDataset("DS_TEST2", candles), contract_master=contract)
    res = engine.run()

    # Pending entry was rejected due to expiry; zero fills executed
    assert engine.expired_trade_attempts >= 1
    assert engine.post_expiry_fills == 0
    assert len(res.trades) == 0
    gate_e = next(g for g in res.quant_gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.PASS
    assert gate_e.evidence["contract_metadata_valid"] is True
    assert gate_e.evidence["lifecycle_violation"] is False
    assert gate_e.evidence["post_expiry_fills"] == 0
    assert gate_e.evidence["contract_valid"] is True


def test_gate_e_test_3_actual_post_expiry_fill() -> None:
    """Test 3 — Actual post-expiry fill: post_expiry_fills > 0 -> Gate E FAIL."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )
    evidence = {
        "contract_master_present": True,
        "contract_id": "NIFTY-SPOT",
        "contract_metadata_valid": True,
        "post_expiry_fills": 1,
        "contract_valid": False,
        "lifecycle_violation": True,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=evidence,
    )
    gate_e = next(g for g in gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.FAIL
    assert gate_e.error_count == 1
    assert any("Gate E Failed" in e for e in errs)


def test_gate_e_test_4_invalid_lot_size() -> None:
    """Test 4 — Invalid lot size: lot_size <= 0 -> Gate E FAIL."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )
    evidence = {
        "contract_master_present": True,
        "contract_id": "NIFTY-SPOT",
        "lot_size": 0,
        "contract_metadata_valid": False,
        "post_expiry_fills": 0,
        "contract_valid": False,
        "lifecycle_violation": True,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=evidence,
    )
    gate_e = next(g for g in gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.FAIL
    assert any("Gate E Failed" in e for e in errs)


def test_gate_e_test_5_invalid_multiplier() -> None:
    """Test 5 — Invalid multiplier: multiplier <= 0 -> Gate E FAIL."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )
    evidence = {
        "contract_master_present": True,
        "contract_id": "NIFTY-SPOT",
        "contract_multiplier": Decimal("0"),
        "contract_metadata_valid": False,
        "post_expiry_fills": 0,
        "contract_valid": False,
        "lifecycle_violation": True,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=evidence,
    )
    gate_e = next(g for g in gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.FAIL
    assert any("Gate E Failed" in e for e in errs)


def test_gate_e_test_6_invalid_contract_metadata() -> None:
    """Test 6 — Invalid contract metadata: contract_metadata_valid = False -> Gate E FAIL."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )
    evidence = {
        "contract_master_present": True,
        "contract_id": "INVALID_METADATA_CONTRACT",
        "contract_metadata_valid": False,
        "post_expiry_fills": 0,
        "contract_valid": False,
        "lifecycle_violation": True,
    }
    gates, status, warns, errs = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        contract_evidence=evidence,
    )
    gate_e = next(g for g in gates if g.gate_id == "Gate E")
    assert gate_e.status == QuantGateStatus.FAIL
    assert any("Gate E Failed" in e for e in errs)


def test_quant_gate_i_deliberate_failures() -> None:
    """Verify Gate I fails on fold overlap, parameter mutation, or chronology violation."""
    t0 = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    dataset = BacktestDataset("DS", [_create_candle(t0, Decimal("24000"))])
    metrics = BacktestMetrics(
        total_return=Decimal("0"),
        total_return_pct=Decimal("0"),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        break_even_trades=0,
        win_rate=Decimal("0"),
        loss_rate=Decimal("0"),
        average_win=Decimal("0"),
        average_loss=Decimal("0"),
        expectancy=Decimal("0"),
        max_drawdown=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_duration_seconds=0,
        average_exposure=Decimal("0"),
        max_exposure=Decimal("0"),
        gross_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage=Decimal("0"),
        net_pnl=Decimal("0"),
    )

    # Fold overlap failure
    evidence_overlap = {
        "fold_count": 3,
        "overlap_count": 1,
        "oos_parameter_mutations": 0,
        "chronology_violations": 0,
    }
    gates_o, status_o, warns_o, errs_o = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        oos_evidence=evidence_overlap,
    )
    gate_i_o = next(g for g in gates_o if g.gate_id == "Gate I")
    assert gate_i_o.status == QuantGateStatus.FAIL

    # Parameter mutation failure
    evidence_mutation = {
        "fold_count": 3,
        "overlap_count": 0,
        "oos_parameter_mutations": 1,
        "chronology_violations": 0,
    }
    gates_m, status_m, warns_m, errs_m = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        oos_evidence=evidence_mutation,
    )
    gate_i_m = next(g for g in gates_m if g.gate_id == "Gate I")
    assert gate_i_m.status == QuantGateStatus.FAIL

    # Chronology violation failure
    evidence_chron = {
        "fold_count": 3,
        "overlap_count": 0,
        "oos_parameter_mutations": 0,
        "chronology_violations": 1,
    }
    gates_c, status_c, warns_c, errs_c = QuantGateEvaluator.evaluate_gates(
        trades=[],
        equity_curve=[],
        metrics=metrics,
        dataset=dataset,
        initial_capital=Decimal("1000000"),
        oos_evidence=evidence_chron,
    )
    gate_i_c = next(g for g in gates_c if g.gate_id == "Gate I")
    assert gate_i_c.status == QuantGateStatus.FAIL
