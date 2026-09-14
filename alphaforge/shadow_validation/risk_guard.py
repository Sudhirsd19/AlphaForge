"""
Institutional Risk Guardrails & Circuit Breakers (Phase 18-G).
Implements institutional risk safety constraints:
1. Portfolio Heat tracking (aggregated active risk exposure).
2. Concentration / instrument position caps.
3. Stale-data signal blocking (fail-closed when feed age > threshold).
4. Intraday drawdown governor (session lockdown on hitting daily loss limit).
5. Volatility-adjusted position sizing (ATR-based risk allocation).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from alphaforge.core.exceptions import DataIntegrityError

if TYPE_CHECKING:
    from datetime import datetime


class InstitutionalRiskGuard:
    """
    Enforces pre-trade and intra-session risk safety invariants.
    """

    def __init__(
        self,
        max_portfolio_heat_pct: Decimal = Decimal("6.00"),  # Max total risk 6% across all positions
        max_single_position_risk_pct: Decimal = Decimal("2.00"),  # Max 2% risk on single trade
        max_daily_drawdown_pct: Decimal = Decimal("3.00"),  # Daily loss limit 3%
        max_data_staleness_seconds: float = 5.0,  # 5000ms staleness limit
        initial_capital: Decimal = Decimal("1000000"),
    ) -> None:
        if max_portfolio_heat_pct <= Decimal("0") or max_daily_drawdown_pct <= Decimal("0"):
            raise DataIntegrityError("Risk limits must be strictly positive")

        self.max_portfolio_heat_pct = max_portfolio_heat_pct
        self.max_single_position_risk_pct = max_single_position_risk_pct
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_data_staleness_seconds = max_data_staleness_seconds
        self.starting_daily_equity = initial_capital
        self.current_equity = initial_capital
        self.peak_daily_equity = initial_capital
        self.daily_lockdown_engaged = False
        self.daily_lockdown_reason: str | None = None

    def update_equity(self, current_equity: Decimal) -> None:
        """Update running equity and evaluate intraday drawdown circuit breaker."""
        self.current_equity = current_equity
        if current_equity > self.peak_daily_equity:
            self.peak_daily_equity = current_equity

        if self.starting_daily_equity > Decimal("0"):
            intraday_loss = self.starting_daily_equity - current_equity
            loss_pct = (intraday_loss / self.starting_daily_equity) * Decimal("100")
            if loss_pct >= self.max_daily_drawdown_pct:
                self.daily_lockdown_engaged = True
                self.daily_lockdown_reason = (
                    f"INTRADAY_DRAWDOWN_BREACH: Loss {loss_pct:.2f}% >= "
                    f"max allowed {self.max_daily_drawdown_pct:.2f}%. Session locked."
                )

    def verify_data_freshness(
        self,
        feed_timestamp: datetime,
        evaluation_timestamp: datetime,
    ) -> tuple[bool, str]:
        """
        Fail-closed check ensuring signal evaluation is blocked if market data is stale.
        """
        age_seconds = (evaluation_timestamp - feed_timestamp).total_seconds()
        if age_seconds < 0:
            return False, f"NEGATIVE_LATENCY: Feed timestamp {feed_timestamp} is in future"
        if age_seconds > self.max_data_staleness_seconds:
            return False, (
                f"STALE_DATA_BLOCKED: Feed age {age_seconds:.3f}s exceeds "
                f"limit of {self.max_data_staleness_seconds:.1f}s"
            )
        return True, "DATA_FRESH"

    def calculate_portfolio_heat(
        self,
        open_positions: list[dict[str, Any]],  # each with "reserved_risk_inr"
    ) -> Decimal:
        """Calculate aggregate portfolio heat percentage."""
        if self.current_equity <= Decimal("0"):
            return Decimal("100.00")
        total_risk = sum(
            (Decimal(str(p.get("reserved_risk_inr", 0))) for p in open_positions), Decimal("0")
        )
        return (total_risk / self.current_equity) * Decimal("100")

    def authorize_order_risk(
        self,
        requested_risk_inr: Decimal,
        open_positions: list[dict[str, Any]],
        feed_timestamp: datetime,
        evaluation_timestamp: datetime,
    ) -> tuple[bool, str]:
        """
        Pre-trade risk authorization gate.
        Checks:
        1. Intraday drawdown governor.
        2. Feed staleness.
        3. Single trade risk percentage limit.
        4. Portfolio heat cap.
        """
        if self.daily_lockdown_engaged:
            return False, self.daily_lockdown_reason or "SESSION_LOCKED"

        fresh, reason = self.verify_data_freshness(feed_timestamp, evaluation_timestamp)
        if not fresh:
            return False, reason

        if self.current_equity <= Decimal("0"):
            return False, "INSUFFICIENT_CAPITAL: Account equity is non-positive"

        single_risk_pct = (requested_risk_inr / self.current_equity) * Decimal("100")
        if single_risk_pct > self.max_single_position_risk_pct:
            return False, (
                f"SINGLE_TRADE_RISK_BREACH: Requested risk {single_risk_pct:.2f}% "
                f"> max {self.max_single_position_risk_pct:.2f}%"
            )

        current_heat = self.calculate_portfolio_heat(open_positions)
        new_heat = current_heat + single_risk_pct
        if new_heat > self.max_portfolio_heat_pct:
            return False, (
                f"PORTFOLIO_HEAT_BREACH: Projected heat {new_heat:.2f}% "
                f"> max limit {self.max_portfolio_heat_pct:.2f}%"
            )

        return True, "RISK_AUTHORIZED"

    @staticmethod
    def calculate_volatility_adjusted_quantity(
        capital: Decimal,
        risk_pct_per_trade: Decimal,
        atr: Decimal,
        atr_multiplier: Decimal = Decimal("1.5"),
        lot_size: int = 50,
        max_lots: int = 10,
    ) -> int:
        """
        Computes position size in contracts dynamically scaled by ATR volatility.
        Quantity = floor((Capital * RiskPct) / (ATR * Multiplier * LotSize)) * LotSize.
        """
        if atr <= Decimal("0") or capital <= Decimal("0") or lot_size <= 0:
            return 0

        risk_budget = capital * (risk_pct_per_trade / Decimal("100"))
        per_contract_risk = atr * atr_multiplier

        if per_contract_risk <= Decimal("0"):
            return 0

        max_contracts = int(risk_budget / per_contract_risk)
        lots = max_contracts // lot_size
        lots = min(lots, max_lots)
        return max(0, lots * lot_size)
