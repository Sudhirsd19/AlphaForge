"""
Market Regime Classification and Regime-Conditioned Stress Testing.
Classifies price and volume time series into:
- Trend: TRENDING_BULL, TRENDING_BEAR, RANGING
- Volatility: HIGH_VOLATILITY, LOW_VOLATILITY
- Volume: HIGH_VOLUME, LOW_VOLUME
Evaluates strategy performance conditional on regime and flags single-regime fragilities.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alphaforge.data.models import MarketCandle

from alphaforge.quant_validation.metrics import (
    calculate_expectancy,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
)
from alphaforge.quant_validation.models import RegimePerformance, RegimeType


class MarketRegimeClassifier:
    """
    Classifies candles into regimes using moving averages, true range, and volume statistics.
    """

    @staticmethod
    def classify_candle(
        candle: MarketCandle,
        sma_short: float,
        sma_long: float,
        atr_current: float,
        atr_mean: float,
        volume_mean: float,
    ) -> list[RegimeType]:
        """
        Classify a candle into simultaneous regime facets (trend, volatility, volume).
        """
        regimes: list[RegimeType] = []

        # 1. Trend
        spread_pct = (sma_short - sma_long) / sma_long if sma_long > 0 else 0.0
        if spread_pct > 0.002:  # > 0.20% upward separation
            regimes.append(RegimeType.TRENDING_BULL)
        elif spread_pct < -0.002:  # < -0.20% downward separation
            regimes.append(RegimeType.TRENDING_BEAR)
        else:
            regimes.append(RegimeType.RANGING)

        # 2. Volatility
        if atr_current > atr_mean * 1.25:
            regimes.append(RegimeType.HIGH_VOLATILITY)
        elif atr_current < atr_mean * 0.80:
            regimes.append(RegimeType.LOW_VOLATILITY)

        # 3. Volume
        if candle.volume > volume_mean * 1.30:
            regimes.append(RegimeType.HIGH_VOLUME)
        elif candle.volume < volume_mean * 0.70:
            regimes.append(RegimeType.LOW_VOLUME)

        return regimes


class RegimeStressTester:
    """
    Groups simulated or historical trades by the regime active at entry time,
    calculating conditioned expectancy, Sharpe ratio, and drawdown.
    """

    @staticmethod
    def evaluate_regimes(
        trades_with_regimes: Sequence[tuple[dict[str, Any], list[RegimeType]]],
    ) -> list[RegimePerformance]:
        """
        trades_with_regimes: list of (trade_dict, active_regimes).
        trade_dict must have "net_pnl" (Decimal) and optional "return_pct" (float).
        """
        # Group trades by regime
        regime_trades: dict[RegimeType, list[dict[str, Any]]] = {r: [] for r in RegimeType}

        for trade, regimes in trades_with_regimes:
            for r in regimes:
                regime_trades[r].append(trade)

        results: list[RegimePerformance] = []

        for r_type, trades in regime_trades.items():
            if not trades:
                continue

            pnls = [Decimal(str(t.get("net_pnl", 0))) for t in trades]
            returns = [float(t.get("return_pct", 0.0)) for t in trades]

            _, win_rate, profit_factor = calculate_expectancy(pnls)
            net_pnl = sum(pnls, Decimal("0"))
            sharpe = calculate_sharpe_ratio(returns) if len(returns) >= 2 else None

            # Equity curve within regime
            running_equity = Decimal("1000000")
            eq_curve = [running_equity]
            for p in pnls:
                running_equity += p
                eq_curve.append(running_equity)
            _, max_dd_pct = calculate_max_drawdown(eq_curve)

            # Failure condition: negative net pnl and loss of > 1.5x average gain or drawdown > 10%
            is_failing = bool(
                net_pnl < Decimal("0")
                and (profit_factor < Decimal("0.6") or max_dd_pct > Decimal("10.0"))
            )

            results.append(
                RegimePerformance(
                    regime=r_type,
                    bar_count=len(trades),  # Proxy for trade exposure
                    trade_count=len(trades),
                    win_rate=round(win_rate * Decimal("100"), 2),
                    profit_factor=round(profit_factor, 2) if profit_factor > 0 else None,
                    net_pnl=net_pnl,
                    sharpe_ratio=sharpe,
                    max_drawdown_pct=round(max_dd_pct, 2),
                    is_regime_failing=is_failing,
                )
            )

        return results
