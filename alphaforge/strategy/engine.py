"""
AlphaForge Pure Deterministic Strategy Engine.
Implements the formal evaluation pipeline defined in STRATEGY_SPECIFICATION.md.
Completely side-effect-free, zero network/broker dependencies, 100% deterministic.
"""

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
    TrendState,
)
from alphaforge.core.models import Candle, StrategySignal
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.rules import (
    calculate_stops_and_targets,
    evaluate_breakout,
    evaluate_candle_geometry,
    evaluate_momentum,
    evaluate_trend_regime,
    evaluate_volatility,
    evaluate_volume_spike,
)


class DeterministicStrategyEngine:
    """
    Pure mathematical strategy engine implementing AF_ORB_MOMENTUM_V1.
    Evaluates market data sequences and produces immutable StrategySignal decisions.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()
        self.config_hash = self.config.compute_config_hash()
        self._emitted_signal_ids: set[str] = set()

    def evaluate(
        self,
        raw_exec_candles: list[Candle],
        raw_conf_candles: list[Candle],
        futures_status: FuturesConfirmationStatus,
        evaluation_timestamp: datetime,
    ) -> StrategySignal:
        """
        Execute deterministic evaluation of the strategy.
        Indexing convention:
          raw_exec_candles[0] = forming candle [0] (QUARANTINED - NEVER USED).
          raw_exec_candles[1] = latest fully closed candle [1] (trigger bar).
          raw_exec_candles[2] = previous fully closed candle [2].
          ...
        """
        # 1. Minimum Data Length Guard
        min_required = max(
            self.config.breakout_lookback + 2,
            self.config.volume_lookback + 2,
            self.config.rsi_period + 2,
            self.config.atr_period + 2,
            self.config.swing_stop_lookback + 1,
        )
        if len(raw_exec_candles) < min_required:
            return self._build_rejection(
                direction=SignalDirection.FLAT,
                signal_ts=evaluation_timestamp,
                eval_ts=evaluation_timestamp,
                entry=Decimal("0"),
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=TrendState.NEUTRAL,
                futures_status=futures_status,
                volume_status="INSUFFICIENT_DATA",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_INSUFFICIENT_HISTORY,
            )

        # 2. Closed-Candle Quarantine: Discard index [0]
        # Any access to index [0] is strictly forbidden.
        closed_exec = raw_exec_candles[1:]
        trigger_candle = closed_exec[0]  # Latest fully closed candle [1]

        signal_timestamp = trigger_candle.timestamp

        # 3. Data Freshness Guard
        stale_threshold = timedelta(seconds=self.config.max_stale_seconds)
        is_stale = (
            evaluation_timestamp < signal_timestamp
            or (evaluation_timestamp - signal_timestamp) > stale_threshold
        )
        if is_stale:
            return self._build_rejection(
                direction=SignalDirection.FLAT,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=TrendState.NEUTRAL,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_DATA_STALE,
            )

        # 4. Multi-Timeframe Alignment: Filter closed confirmation candles
        # Only candles fully closed on or before trigger candle close timestamp are eligible.
        eligible_conf = [c for c in raw_conf_candles if c.timestamp <= signal_timestamp]
        # If forming candle was present in raw_conf_candles, strip if not closed
        eligible_conf_closed = [c for c in eligible_conf if c.is_closed]

        # Explicitly sort confirmation candles chronologically to prevent inverted EMA evaluation.
        # Use secondary tuple (timestamp, open, high, low, close, volume) for deterministic order.
        sorted_conf_closed = sorted(
            eligible_conf_closed,
            key=lambda c: (c.timestamp, c.open, c.high, c.low, c.close, c.volume),
        )
        # Deduplicate identical timestamps deterministically
        deduped_conf_closed: list[Candle] = []
        seen_timestamps: set[datetime] = set()
        for c in sorted_conf_closed:
            if c.timestamp not in seen_timestamps:
                deduped_conf_closed.append(c)
                seen_timestamps.add(c.timestamp)

        if len(deduped_conf_closed) < self.config.trend_ema_slow:
            return self._build_rejection(
                direction=SignalDirection.FLAT,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=TrendState.NEUTRAL,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_MISSING_TIMEFRAME,
            )

        trend_state, _, _ = evaluate_trend_regime(
            deduped_conf_closed, self.config.trend_ema_fast, self.config.trend_ema_slow
        )

        if trend_state == TrendState.NEUTRAL:
            return self._build_rejection(
                direction=SignalDirection.FLAT,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_TREND,
            )

        direction = (
            SignalDirection.LONG if trend_state == TrendState.BULLISH else SignalDirection.SHORT
        )

        # 5. Chronological Closed Series for Technical Calculations
        # closed_exec[0] is [1], closed_exec[-1] is oldest.
        # Reverse to get chronological order: chrono_closed[-1] is [1] (latest closed).
        chrono_closed = list(reversed(closed_exec))

        # 6. Breakout Detection
        is_long_bo, is_short_bo, resistance, support = evaluate_breakout(
            chrono_closed, self.config.breakout_lookback
        )
        if direction == SignalDirection.LONG and not is_long_bo:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_BREAKOUT,
            )
        if direction == SignalDirection.SHORT and not is_short_bo:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_BREAKOUT,
            )

        # 7. Confirmation Candle Geometry
        is_geom_valid, _, _ = evaluate_candle_geometry(
            trigger_candle,
            direction,
            self.config.min_body_ratio,
            self.config.min_close_location_ratio,
        )
        if not is_geom_valid:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status="NOT_EVALUATED",
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_CANDLE_GEOMETRY,
            )

        # 8. Relative Volume Filter
        is_vol_confirmed, vol_ratio = evaluate_volume_spike(
            chrono_closed, self.config.volume_lookback, self.config.min_relative_volume
        )
        vol_status = "CONFIRMED" if is_vol_confirmed else "BELOW_THRESHOLD"
        if not is_vol_confirmed:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_VOLUME,
            )

        # 9. Momentum Filter (RSI)
        rsi_min = (
            self.config.rsi_long_min
            if direction == SignalDirection.LONG
            else self.config.rsi_short_min
        )
        rsi_max = (
            self.config.rsi_long_max
            if direction == SignalDirection.LONG
            else self.config.rsi_short_max
        )
        is_mom_valid, _ = evaluate_momentum(
            chrono_closed,
            direction,
            self.config.rsi_period,
            rsi_min,
            rsi_max,
        )
        if not is_mom_valid:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_MOMENTUM,
            )

        # 10. Volatility Filter (ATR)
        is_volat_valid, current_atr = evaluate_volatility(
            chrono_closed, self.config.atr_period, self.config.atr_min_pct, self.config.atr_max_pct
        )
        if not is_volat_valid:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_VOLATILITY,
            )

        # 11. Futures Confirmation Gate
        if futures_status != FuturesConfirmationStatus.CONFIRMED:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=trigger_candle.close,
                stop=Decimal("0"),
                target=Decimal("0"),
                risk_dist=Decimal("0"),
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_FUTURES_CONFIRMATION,
            )

        # 12. Stop-Loss & Target Calculations
        swing_candles = closed_exec[: self.config.swing_stop_lookback]
        entry, stop, target, risk_distance = calculate_stops_and_targets(
            trigger_candle,
            swing_candles,
            current_atr,
            direction,
            self.config.atr_stop_multiplier,
            self.config.target_risk_multiple,
        )

        min_risk = entry * self.config.min_risk_distance_pct
        max_risk = entry * self.config.max_risk_distance_pct

        if risk_distance < min_risk or risk_distance > max_risk or stop <= Decimal("0"):
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=entry,
                stop=stop,
                target=target,
                risk_dist=risk_distance,
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_INVALID_STOP,
            )

        is_invalid_target = (
            target <= Decimal("0")
            or (direction == SignalDirection.LONG and target <= entry)
            or (direction == SignalDirection.SHORT and target >= entry)
        )
        if is_invalid_target:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=entry,
                stop=stop,
                target=target,
                risk_dist=risk_distance,
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.REJECT,
                rejection_code=RejectionCode.REJECT_INVALID_TARGET,
            )

        # 13. Deduplication and Signal ID
        signal_id = self.compute_signal_id(direction, signal_timestamp)
        if signal_id in self._emitted_signal_ids:
            return self._build_rejection(
                direction=direction,
                signal_ts=signal_timestamp,
                eval_ts=evaluation_timestamp,
                entry=entry,
                stop=stop,
                target=target,
                risk_dist=risk_distance,
                trend=trend_state,
                futures_status=futures_status,
                volume_status=vol_status,
                decision=StrategyDecision.DUPLICATE,
                rejection_code=RejectionCode.REJECT_DUPLICATE,
            )

        self._emitted_signal_ids.add(signal_id)

        # 14. Emit ACCEPT Signal
        return StrategySignal(
            signal_id=signal_id,
            strategy_id=self.config.strategy_id,
            strategy_version=self.config.strategy_version,
            symbol=self.config.symbol,
            direction=direction,
            signal_timestamp=signal_timestamp,
            evaluation_timestamp=evaluation_timestamp,
            entry_reference=entry,
            stop_reference=stop,
            target_reference=target,
            risk_distance=risk_distance,
            trend_state=trend_state,
            basis_status=futures_status,
            volume_status=vol_status,
            decision=StrategyDecision.ACCEPT,
            rejection_code=RejectionCode.REJECT_NONE,
            config_hash=self.config_hash,
            data_version=1,
        )

    def compute_signal_id(self, direction: SignalDirection, signal_timestamp: datetime) -> str:
        """
        Derive deterministic signal ID strictly from immutable execution attributes.
        """
        payload = (
            f"{self.config.strategy_id}:{self.config.strategy_version}:{self.config.symbol}:"
            f"{direction.value}:{signal_timestamp.isoformat()}:{self.config_hash}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _build_rejection(
        self,
        direction: SignalDirection,
        signal_ts: datetime,
        eval_ts: datetime,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
        risk_dist: Decimal,
        trend: TrendState,
        futures_status: FuturesConfirmationStatus,
        volume_status: str,
        decision: StrategyDecision,
        rejection_code: RejectionCode,
    ) -> StrategySignal:
        """Helper to construct rejection signal with deterministic ID."""
        signal_id = self.compute_signal_id(direction, signal_ts)
        return StrategySignal(
            signal_id=signal_id,
            strategy_id=self.config.strategy_id,
            strategy_version=self.config.strategy_version,
            symbol=self.config.symbol,
            direction=direction,
            signal_timestamp=signal_ts,
            evaluation_timestamp=eval_ts,
            entry_reference=entry,
            stop_reference=stop,
            target_reference=target,
            risk_distance=risk_dist,
            trend_state=trend,
            basis_status=futures_status,
            volume_status=volume_status,
            decision=decision,
            rejection_code=rejection_code,
            config_hash=self.config_hash,
            data_version=1,
        )
