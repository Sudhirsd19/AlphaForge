"""
Rolling Walk-Forward Analysis Engine.
Implements multi-window rolling Walk-Forward Optimization (WFO) testing:
Train (Optimization) -> Validate (Hyperparameter Confirmation) -> Test (Pure Out-of-Sample).
Calculates Walk-Forward Efficiency (WFE) and degradation statistics.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from alphaforge.data.models import MarketCandle

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.quant_validation.models import WalkForwardFold, WalkForwardReport


class WalkForwardEngine:
    """
    Orchestrates anchored or rolling Walk-Forward testing across multiple temporal folds.
    """

    def __init__(
        self,
        num_folds: int = 4,
        train_pct: float = 0.50,
        val_pct: float = 0.20,
        test_pct: float = 0.30,
        anchored: bool = False,
    ) -> None:
        if num_folds < 2:
            raise DataIntegrityError(f"num_folds must be >= 2, got {num_folds}")
        total = train_pct + val_pct + test_pct
        if abs(total - 1.0) > 0.001:
            raise DataIntegrityError(f"Fold percentages must sum to 1.0, got {total}")

        self.num_folds = num_folds
        self.train_pct = train_pct
        self.val_pct = val_pct
        self.test_pct = test_pct
        self.anchored = anchored

    def generate_folds(
        self,
        candles: Sequence[MarketCandle],
    ) -> list[tuple[list[MarketCandle], list[MarketCandle], list[MarketCandle]]]:
        """
        Partition candles into (train, val, test) windows for each fold.
        """
        n = len(candles)
        min_required = self.num_folds * 30
        if n < min_required:
            raise DataIntegrityError(
                f"Insufficient candles ({n}) for {self.num_folds} folds (need >= {min_required})"
            )

        window_size = n // (self.num_folds + 1)
        folds_data = []

        for i in range(self.num_folds):
            start_idx = 0 if self.anchored else i * (window_size // 2)
            fold_total_len = (i + 2) * window_size if self.anchored else window_size * 2
            end_idx = min(n, start_idx + fold_total_len)

            fold_candles = candles[start_idx:end_idx]
            fold_len = len(fold_candles)
            if fold_len < 30:
                continue

            n_train = int(fold_len * self.train_pct)
            n_val = int(fold_len * self.val_pct)

            train_set = list(fold_candles[:n_train])
            val_set = list(fold_candles[n_train : n_train + n_val])
            test_set = list(fold_candles[n_train + n_val :])

            if train_set and test_set:
                folds_data.append((train_set, val_set, test_set))

        if not folds_data:
            raise DataIntegrityError("Could not generate valid non-empty folds")

        return folds_data

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        evaluator_fn: Callable[[Sequence[MarketCandle]], dict[str, Any]],
    ) -> WalkForwardReport:
        """
        Run walk-forward analysis using a supplied evaluation function.
        evaluator_fn takes candles and returns a dict with:
        {"trade_count": int, "sharpe": float | None, "net_pnl": Decimal}
        """
        partitioned_folds = self.generate_folds(candles)
        evaluated_folds: list[WalkForwardFold] = []

        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []
        positive_oos_count = 0

        for idx, (train_c, val_c, test_c) in enumerate(partitioned_folds):
            is_metrics = evaluator_fn(train_c)
            oos_metrics = evaluator_fn(test_c)

            is_s = is_metrics.get("sharpe")
            oos_s = oos_metrics.get("sharpe")
            eff_ratio: float | None = None
            if is_s is not None and is_s > 0 and oos_s is not None:
                eff_ratio = round(oos_s / is_s, 4)

            if is_s is not None:
                is_sharpes.append(is_s)
            if oos_s is not None:
                oos_sharpes.append(oos_s)

            oos_pnl = oos_metrics.get("net_pnl", Decimal("0"))
            if oos_pnl > Decimal("0"):
                positive_oos_count += 1

            v_start = (
                val_c[0].exchange_timestamp if val_c else train_c[-1].exchange_timestamp
            )
            v_end = (
                val_c[-1].exchange_timestamp if val_c else train_c[-1].exchange_timestamp
            )

            evaluated_folds.append(
                WalkForwardFold(
                    fold_index=idx,
                    train_start=train_c[0].exchange_timestamp,
                    train_end=train_c[-1].exchange_timestamp,
                    val_start=v_start,
                    val_end=v_end,
                    test_start=test_c[0].exchange_timestamp,
                    test_end=test_c[-1].exchange_timestamp,
                    is_trades=int(is_metrics.get("trade_count", 0)),
                    oos_trades=int(oos_metrics.get("trade_count", 0)),
                    is_sharpe=is_s,
                    oos_sharpe=oos_s,
                    is_profit_factor=is_metrics.get("profit_factor"),
                    oos_profit_factor=oos_metrics.get("profit_factor"),
                    is_net_pnl=Decimal(str(is_metrics.get("net_pnl", 0))),
                    oos_net_pnl=Decimal(str(oos_pnl)),
                    efficiency_ratio=eff_ratio,
                )
            )

        mean_is = sum(is_sharpes) / len(is_sharpes) if is_sharpes else None
        mean_oos = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else None

        wfe: float | None = None
        if mean_is is not None and mean_is > 0 and mean_oos is not None:
            wfe = round(mean_oos / mean_is, 4)

        pos_ratio = positive_oos_count / len(evaluated_folds) if evaluated_folds else 0.0
        # Stable requires: positive OOS Sharpe, positive OOS ratio >= 60%, and WFE >= 0.50
        is_stable = bool(
            wfe is not None
            and wfe >= 0.50
            and pos_ratio >= 0.60
            and mean_oos is not None
            and mean_oos > 0
        )

        return WalkForwardReport(
            total_folds=len(evaluated_folds),
            folds=evaluated_folds,
            mean_is_sharpe=round(mean_is, 4) if mean_is is not None else None,
            mean_oos_sharpe=round(mean_oos, 4) if mean_oos is not None else None,
            walk_forward_efficiency=wfe,
            positive_oos_ratio=round(pos_ratio, 4),
            is_stable=is_stable,
        )
