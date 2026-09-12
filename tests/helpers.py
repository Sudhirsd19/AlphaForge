"""
Test helpers for generating deterministic synthetic candle sequences.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from alphaforge.core.models import Candle


def create_candle(
    timestamp: datetime,
    open_price: str | Decimal,
    high_price: str | Decimal,
    low_price: str | Decimal,
    close_price: str | Decimal,
    volume: int = 1000,
    is_closed: bool = True,
) -> Candle:
    """Helper to create a validated Candle instance."""
    return Candle(
        timestamp=timestamp,
        open=Decimal(str(open_price)),
        high=Decimal(str(high_price)),
        low=Decimal(str(low_price)),
        close=Decimal(str(close_price)),
        volume=volume,
        is_closed=is_closed,
    )


def generate_candle_series(
    base_time: datetime,
    count: int,
    base_price: Decimal = Decimal("24000.00"),
    step_price: Decimal = Decimal("10.00"),
    interval_minutes: int = 3,
    volume_base: int = 1000,
    trend_type: str = "bullish",
) -> list[Candle]:
    """
    Generate a series of closed candles followed by index [0] forming candle.
    Index [0] is the latest (forming) candle.
    Index [1] is the latest closed candle.
    Index [2] is the previous closed candle...
    Returns list formatted in canonical indexing convention [0, 1, 2, ...].
    """
    chrono_candles: list[Candle] = []
    current_time = base_time - timedelta(minutes=interval_minutes * count)
    current_price = base_price

    for i in range(count):
        current_time += timedelta(minutes=interval_minutes)
        if trend_type == "bullish":
            current_price += step_price
            open_p = current_price - Decimal("5.00")
            high_p = current_price + Decimal("8.00")
            low_p = current_price - Decimal("6.00")
            close_p = current_price + Decimal("5.00")
        elif trend_type == "bearish":
            current_price -= step_price
            open_p = current_price + Decimal("5.00")
            high_p = current_price + Decimal("6.00")
            low_p = current_price - Decimal("8.00")
            close_p = current_price - Decimal("5.00")
        else:  # flat / neutral
            open_p = current_price - Decimal("2.00")
            high_p = current_price + Decimal("5.00")
            low_p = current_price - Decimal("5.00")
            close_p = current_price + Decimal("1.00")

        chrono_candles.append(
            create_candle(
                timestamp=current_time,
                open_price=open_p,
                high_price=high_p,
                low_price=low_p,
                close_price=close_p,
                volume=volume_base + (i * 10),
                is_closed=True,
            )
        )

    # Forming candle [0]
    forming_time = current_time + timedelta(minutes=interval_minutes)
    forming_candle = create_candle(
        timestamp=forming_time,
        open_price=current_price,
        high_price=current_price + Decimal("10.00"),
        low_price=current_price - Decimal("10.00"),
        close_price=current_price + Decimal("2.00"),
        volume=500,
        is_closed=False,
    )

    # Return with forming candle at index [0], latest closed at index [1], previous closed at [2]...
    result = [forming_candle] + list(reversed(chrono_candles))
    return result
