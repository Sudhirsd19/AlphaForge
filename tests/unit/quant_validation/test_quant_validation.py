"""
Comprehensive Unit Tests for Phase 18 Quantitative Validation Engine.
Tests:
- Sample size caveats and sufficiency guards
- Accurate mathematical expectancy, Sharpe, Sortino, max drawdown
- Temporal IS/OOS split with embargo and zero-leakage enforcement
- Rolling Walk-Forward Analysis and Walk-Forward Efficiency (WFE)
- Combinatorial Purged & Embargoed Cross-Validation (CPCV)
- Market regime classification and conditional stress testing
- Parameter perturbation and cliff-edge detection
- Cost sensitivity and break-even slippage calculation
- Monte Carlo sequence permutation and return bootstrap
- Institutional robustness gate verdicts
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

import pytest

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.data.enums import InstrumentType
from alphaforge.data.models import MarketCandle
from alphaforge.quant_validation import (
    CombinatorialPurgedCV,
    CostSensitivityEngine,
    InstitutionalRobustnessGate,
    MarketRegimeClassifier,
    MonteCarloEngine,
    ParameterSensitivityAnalyzer,
    RegimeStressTester,
    RegimeType,
    RobustnessGateResult,
    WalkForwardEngine,
    calculate_expectancy,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    create_temporal_split,
    evaluate_sample_sufficiency,
)


def _make_candles(count: int, start_price: Decimal = Decimal("24000.00")) -> list[MarketCandle]:
    base_time = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    candles = []
    p = start_price
    for i in range(count):
        candles.append(
            MarketCandle(
                symbol="NIFTY",
                instrument_type=InstrumentType.INDEX,
                contract_id="NIFTY-SPOT",
                exchange_timestamp=base_time + timedelta(minutes=i),
                received_timestamp=base_time + timedelta(minutes=i),
                timeframe="1m",
                open=p,
                high=p + Decimal("10.00"),
                low=p - Decimal("10.00"),
                close=p + Decimal("2.00"),
                volume=1000 + (i * 10),
                source="SYNTHETIC_BENCHMARK",
            )
        )
        p += Decimal("2.00")
    return candles


# --- 1. Metrics & Sample Sufficiency ---


def test_sample_sufficiency_guard() -> None:
    # N < 30
    is_suff, caveat = evaluate_sample_sufficiency(25, duration_days=45)
    assert is_suff is False
    assert caveat is not None
    assert "below institutional minimum" in caveat

    # duration < 30
    is_suff_dur, caveat_dur = evaluate_sample_sufficiency(50, duration_days=15)
    assert is_suff_dur is False
    assert caveat_dur is not None
    assert "less than 30-day minimum" in caveat_dur

    # Sufficient
    is_suff_ok, caveat_ok = evaluate_sample_sufficiency(100, duration_days=60)
    assert is_suff_ok is True
    assert caveat_ok is None


def test_expectancy_and_profit_factor() -> None:
    pnls = [
        Decimal("100.00"),
        Decimal("150.00"),
        Decimal("-50.00"),
        Decimal("200.00"),
        Decimal("-100.00"),
    ]
    expectancy, win_rate, profit_factor = calculate_expectancy(pnls)
    # 3 wins (100, 150, 200) -> sum=450, avg_win = 150
    # 2 losses (50, 100) -> sum=150, avg_loss = 75
    # win_rate = 3/5 = 0.60, loss_rate = 2/5 = 0.40
    # expectancy = 0.6 * 150 - 0.4 * 75 = 90 - 30 = 60
    assert expectancy == Decimal("60.00")
    assert win_rate == Decimal("0.60")
    assert profit_factor == Decimal("3.00")  # 450 / 150


def test_sharpe_and_sortino_ratio() -> None:
    returns = [0.01, 0.02, -0.005, 0.015, -0.002, 0.025, 0.008, 0.012]
    sharpe = calculate_sharpe_ratio(returns, risk_free_rate_annual=0.065, periods_per_year=252)
    assert sharpe is not None
    assert sharpe > 0.0

    sortino = calculate_sortino_ratio(returns, target_return=0.0, periods_per_year=252)
    assert sortino is not None
    assert sortino > sharpe  # Sortino is typically higher when upside variance is large


def test_max_drawdown_calculation() -> None:
    equity = [
        Decimal("1000000"),
        Decimal("1050000"),  # peak
        Decimal("1020000"),  # dd 30k
        Decimal("980000"),  # dd 70k -> 6.666%
        Decimal("1010000"),
        Decimal("1100000"),  # new peak
        Decimal("1080000"),
    ]
    max_dd, max_dd_pct = calculate_max_drawdown(equity)
    assert max_dd == Decimal("70000")
    assert round(max_dd_pct, 2) == Decimal("6.67")


# --- 2. Temporal Split & No Leakage ---


def test_temporal_split_chronology_and_embargo() -> None:
    candles = _make_candles(200)
    split, train, oos = create_temporal_split(candles, train_ratio=0.70, embargo_seconds=1800)

    assert len(train) == 140
    assert split.train_start == train[0].exchange_timestamp
    assert split.train_end == train[-1].exchange_timestamp
    assert split.oos_start == oos[0].exchange_timestamp
    assert split.oos_end == oos[-1].exchange_timestamp
    # Verify strict temporal separation with embargo
    assert split.oos_start >= split.train_end + timedelta(seconds=1800)


def test_temporal_split_rejects_unsorted_candles() -> None:
    candles = _make_candles(50)
    # Swap two candles
    candles[10], candles[11] = candles[11], candles[10]
    with pytest.raises(DataIntegrityError, match="not in chronological order"):
        create_temporal_split(candles)


# --- 3. Walk-Forward Engine ---


def test_walk_forward_engine() -> None:
    candles = _make_candles(200)
    engine = WalkForwardEngine(num_folds=3, train_pct=0.50, val_pct=0.20, test_pct=0.30)

    def dummy_evaluator(c_subset: Sequence[MarketCandle]) -> dict:
        return {
            "trade_count": len(c_subset) // 5,
            "sharpe": 1.5,
            "profit_factor": 1.8,
            "net_pnl": Decimal("5000.00"),
        }

    report = engine.evaluate(candles, dummy_evaluator)
    assert report.total_folds == 3
    assert report.walk_forward_efficiency is not None
    assert report.walk_forward_efficiency == 1.0  # OOS 1.5 / IS 1.5
    assert report.is_stable is True


# --- 4. Combinatorial Purged & Embargoed CV (CPCV) ---


def test_combinatorial_purged_cv() -> None:
    cpcv = CombinatorialPurgedCV(
        n_groups=5, k_test_groups=2, purge_window_bars=3, embargo_window_bars=5
    )
    partitions = cpcv.split(total_bars=100)
    # C(5, 2) = 10 combinations
    assert len(partitions) == 10
    for p in partitions:
        assert p.test_bar_count > 0
        assert p.train_bar_count > 0
        assert p.purged_bar_count >= 0
        assert p.embargoed_bar_count >= 0
        assert (
            p.train_bar_count + p.test_bar_count + p.purged_bar_count + p.embargoed_bar_count == 100
        )


# --- 5. Regime Classification & Stress ---


def test_market_regime_classification() -> None:
    c = _make_candles(1)[0]
    # Bull trending, high volatility, high volume
    regimes = MarketRegimeClassifier.classify_candle(
        c,
        sma_short=102.0,
        sma_long=100.0,
        atr_current=20.0,
        atr_mean=10.0,
        volume_mean=500.0,
    )
    assert RegimeType.TRENDING_BULL in regimes
    assert RegimeType.HIGH_VOLATILITY in regimes
    assert RegimeType.HIGH_VOLUME in regimes


def test_regime_stress_tester() -> None:
    trades_data = [
        ({"net_pnl": Decimal("1000"), "return_pct": 0.01}, [RegimeType.TRENDING_BULL]),
        ({"net_pnl": Decimal("1500"), "return_pct": 0.015}, [RegimeType.TRENDING_BULL]),
        ({"net_pnl": Decimal("-800"), "return_pct": -0.008}, [RegimeType.RANGING]),
        ({"net_pnl": Decimal("-1200"), "return_pct": -0.012}, [RegimeType.RANGING]),
    ]
    results = RegimeStressTester.evaluate_regimes(trades_data)
    assert len(results) == 2

    bull_res = next(r for r in results if r.regime == RegimeType.TRENDING_BULL)
    ranging_res = next(r for r in results if r.regime == RegimeType.RANGING)

    assert bull_res.net_pnl == Decimal("2500")
    assert bull_res.is_regime_failing is False

    assert ranging_res.net_pnl == Decimal("-2000")
    assert ranging_res.is_regime_failing is True


# --- 6. Parameter Sensitivity & Cliff-Edges ---


def test_parameter_sensitivity_cliff_edge() -> None:
    # Function with a sudden cliff at param < 19
    def fragile_eval(p: float) -> float:
        return 2.0 if p >= 19.0 else 0.5

    result = ParameterSensitivityAnalyzer.analyze_parameter(
        parameter_name="breakout_period",
        baseline_value=20.0,
        eval_fn=fragile_eval,
    )
    # At -10% (p=18.0), metric drops from 2.0 to 0.5 (75% degradation) -> cliff edge!
    assert result.cliff_edge_detected is True
    assert result.max_metric_degradation_pct >= 40.0


# --- 7. Cost Sensitivity Stress ---


def test_cost_sensitivity_stress() -> None:
    trades = [
        {
            "gross_pnl": Decimal("5000"),
            "fees": Decimal("200"),
            "slippage": Decimal("100"),
            "turnover": Decimal("500000"),
        },
        {
            "gross_pnl": Decimal("4000"),
            "fees": Decimal("200"),
            "slippage": Decimal("100"),
            "turnover": Decimal("500000"),
        },
    ]
    res = CostSensitivityEngine.stress_test_trades(trades)
    assert res.baseline_net_pnl == Decimal("8400")
    assert res.robust_to_2x_costs is True
    assert res.robust_to_3x_costs is True
    assert res.break_even_slippage_bps is not None
    assert res.break_even_slippage_bps > 50.0


# --- 8. Monte Carlo Bootstrap ---


def test_monte_carlo_bootstrap() -> None:
    pnls = [Decimal(str(x * 100)) for x in [1, 2, -1, 3, -1, 2, -2, 4, 1, 3, -1, 2, 5, -2, 3]]
    mc = MonteCarloEngine(iterations=200, seed=123)
    dist = mc.run_validation(pnls)

    assert dist.trade_count == len(pnls)
    assert dist.iterations == 200
    assert dist.p95_drawdown_pct >= dist.mean_drawdown_pct
    assert dist.p99_drawdown_pct >= dist.p95_drawdown_pct
    assert dist.p_value_zero_expectancy < 0.05  # Strong positive expectancy


# --- 9. Institutional Robustness Gate ---


def test_institutional_gate_robust() -> None:
    # 40 trades with positive edge and stable distribution
    pnls = [Decimal(str(x * 50)) for x in [2, 3, -1, 4, -1, 3, 2, -2, 5, 1] * 4]
    cost_res = CostSensitivityEngine.stress_test_trades(
        [
            {"gross_pnl": p * Decimal("1.2"), "fees": Decimal("10"), "slippage": Decimal("10")}
            for p in pnls
        ]
    )
    mc = MonteCarloEngine(iterations=200, seed=42)
    dist = mc.run_validation(pnls)

    report = InstitutionalRobustnessGate.evaluate(
        trades_pnl=pnls,
        duration_days=45,
        cost_stress=cost_res,
        monte_carlo=dist,
    )
    assert report.overall_gate == RobustnessGateResult.ROBUST
    assert report.has_sufficient_sample is True
    assert report.expectancy > Decimal("0")


def test_institutional_gate_insufficient_data() -> None:
    # Only 10 trades -> N < 30
    pnls = [Decimal("100")] * 10
    report = InstitutionalRobustnessGate.evaluate(trades_pnl=pnls, duration_days=10)
    assert report.overall_gate == RobustnessGateResult.INSUFFICIENT_DATA
    assert report.has_sufficient_sample is False
    assert report.sample_size_caveat is not None


def test_institutional_gate_failed_on_negative_expectancy() -> None:
    # Losing strategy
    pnls = [Decimal("-100")] * 35
    report = InstitutionalRobustnessGate.evaluate(trades_pnl=pnls, duration_days=45)
    assert report.overall_gate == RobustnessGateResult.FAILED
    assert any("expectancy is zero or negative" in r for r in report.gate_reasons)
