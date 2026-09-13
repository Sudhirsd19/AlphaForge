"""
AlphaForge Realistic Shadow Execution Engine (Phase 17 PS-43, PS-44).
Models bid-ask spread, adverse slippage, execution latency, market gaps,
and deterministic partial/delayed fill cases (Cases A through I).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from alphaforge.execution.enums import OrderSide
from alphaforge.shadow_validation.enums import FillExecutionType
from alphaforge.shadow_validation.models import RealisticFillRecord


class RealisticShadowExecutionEngine:
    """
    Simulates real-market execution frictions under shadow validation.
    Handles immediate full fills, partial fills, cancellations, delayed fills,
    rapid price movements, and partial SL/TP triggers.
    """

    def __init__(
        self,
        base_spread: Decimal = Decimal("0.10"),
        base_slippage_pct: Decimal = Decimal("0.0002"),
        simulated_latency_ms: int = 15,
        fee_per_lot: Decimal = Decimal("20.00"),
    ) -> None:
        self.base_spread = base_spread
        self.base_slippage_pct = base_slippage_pct
        self.simulated_latency_ms = simulated_latency_ms
        self.fee_per_lot = fee_per_lot
        self.fill_history: list[RealisticFillRecord] = []

    def execute_shadow_fill(
        self,
        order_id: str,
        trade_id: str,
        causation_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
        reference_price: Decimal,
        decision_timestamp: datetime,
        execution_type: FillExecutionType = FillExecutionType.IMMEDIATE_FULL,
        execution_reason: str = "SIGNAL_ENTRY",
        partial_ratio: Decimal = Decimal("0.50"),
    ) -> list[RealisticFillRecord]:
        """
        Execute deterministic simulated fill under realistic friction models.
        Supports Cases A through I.
        """
        submission_ts = decision_timestamp + timedelta(milliseconds=2)
        fill_ts = submission_ts + timedelta(milliseconds=self.simulated_latency_ms)

        half_spread = self.base_spread / Decimal("2")
        slippage_delta = reference_price * self.base_slippage_pct

        if execution_type == FillExecutionType.RAPID_MOVEMENT:
            slippage_delta *= Decimal("3")

        if side == OrderSide.BUY:
            simulated_fill_price = reference_price + half_spread + slippage_delta
        else:
            simulated_fill_price = reference_price - half_spread - slippage_delta

        # Round to tick size 0.05
        simulated_fill_price = (simulated_fill_price * Decimal("20")).quantize(
            Decimal("1")
        ) / Decimal("20")

        records: list[RealisticFillRecord] = []
        lots = max(1, quantity // 50)
        fees = self.fee_per_lot * Decimal(str(lots))

        if side == OrderSide.BUY:
            slip_val = simulated_fill_price - reference_price
        else:
            slip_val = reference_price - simulated_fill_price

        if execution_type in (
            FillExecutionType.IMMEDIATE_FULL,
            FillExecutionType.RAPID_MOVEMENT,
            FillExecutionType.DELAYED,
        ):
            del_ts = (
                fill_ts
                if execution_type != FillExecutionType.DELAYED
                else fill_ts + timedelta(milliseconds=150)
            )
            lat_val = (
                self.simulated_latency_ms if execution_type != FillExecutionType.DELAYED else 165
            )
            rec = RealisticFillRecord(
                fill_id=f"FILL-{order_id}-1",
                order_id=order_id,
                trade_id=trade_id,
                causation_id=causation_id,
                symbol=symbol,
                side=side,
                decision_timestamp=decision_timestamp,
                submission_timestamp=submission_ts,
                fill_timestamp=del_ts,
                reference_price=reference_price,
                simulated_fill_price=simulated_fill_price,
                quantity=quantity,
                filled_quantity=quantity,
                remaining_quantity=0,
                slippage=slip_val,
                latency_ms=lat_val,
                spread=self.base_spread,
                execution_type=execution_type,
                execution_reason=execution_reason,
                fees=fees,
            )
            records.append(rec)

        elif execution_type in (
            FillExecutionType.PARTIAL_REMAINING,
            FillExecutionType.PARTIAL_EXIT,
            FillExecutionType.PARTIAL_TP,
            FillExecutionType.PARTIAL_SL,
        ):
            part_qty = int(quantity * partial_ratio)
            rem_qty = quantity - part_qty

            rec1 = RealisticFillRecord(
                fill_id=f"FILL-{order_id}-1",
                order_id=order_id,
                trade_id=trade_id,
                causation_id=causation_id,
                symbol=symbol,
                side=side,
                decision_timestamp=decision_timestamp,
                submission_timestamp=submission_ts,
                fill_timestamp=fill_ts,
                reference_price=reference_price,
                simulated_fill_price=simulated_fill_price,
                quantity=quantity,
                filled_quantity=part_qty,
                remaining_quantity=rem_qty,
                slippage=slip_val,
                latency_ms=self.simulated_latency_ms,
                spread=self.base_spread,
                execution_type=execution_type,
                execution_reason=f"{execution_reason}_PARTIAL",
                fees=fees * Decimal("0.5"),
            )
            records.append(rec1)

            if execution_type == FillExecutionType.PARTIAL_REMAINING:
                rem_fill_p = (
                    simulated_fill_price + Decimal("0.05")
                    if side == OrderSide.BUY
                    else simulated_fill_price - Decimal("0.05")
                )
                rec2 = RealisticFillRecord(
                    fill_id=f"FILL-{order_id}-2",
                    order_id=order_id,
                    trade_id=trade_id,
                    causation_id=causation_id,
                    symbol=symbol,
                    side=side,
                    decision_timestamp=decision_timestamp,
                    submission_timestamp=submission_ts,
                    fill_timestamp=fill_ts + timedelta(milliseconds=25),
                    reference_price=reference_price,
                    simulated_fill_price=rem_fill_p,
                    quantity=quantity,
                    filled_quantity=rem_qty,
                    remaining_quantity=0,
                    slippage=slip_val,
                    latency_ms=self.simulated_latency_ms + 25,
                    spread=self.base_spread,
                    execution_type=FillExecutionType.PARTIAL_REMAINING,
                    execution_reason=f"{execution_reason}_REMAINDER",
                    fees=fees * Decimal("0.5"),
                )
                records.append(rec2)

        elif execution_type == FillExecutionType.PARTIAL_CANCEL:
            part_qty = int(quantity * partial_ratio)
            rem_qty = quantity - part_qty
            rec = RealisticFillRecord(
                fill_id=f"FILL-{order_id}-1",
                order_id=order_id,
                trade_id=trade_id,
                causation_id=causation_id,
                symbol=symbol,
                side=side,
                decision_timestamp=decision_timestamp,
                submission_timestamp=submission_ts,
                fill_timestamp=fill_ts,
                reference_price=reference_price,
                simulated_fill_price=simulated_fill_price,
                quantity=quantity,
                filled_quantity=part_qty,
                remaining_quantity=rem_qty,
                slippage=slip_val,
                latency_ms=self.simulated_latency_ms,
                spread=self.base_spread,
                execution_type=FillExecutionType.PARTIAL_CANCEL,
                execution_reason=f"{execution_reason}_PARTIAL_CANCELLED",
                fees=fees * Decimal("0.5"),
            )
            records.append(rec)

        self.fill_history.extend(records)
        return records
