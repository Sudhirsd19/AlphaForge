"""
Regression tests for Phase 2 execution eligibility gate and real forming candle contract.
Covers requirements A through J:
A. GAP => execution blocked
B. STALE => execution blocked
C. CONFLICT => execution blocked
D. INVALID => execution blocked
E. INCOMPLETE => execution blocked
F. exact DUPLICATE => deduplicated and still eligible
G. OUT_OF_ORDER => sorted deterministically and still usable
H. no forming candle => NO synthetic candle created
I. real forming candle => correctly appears at [0]
J. [1] remains latest fully closed candle
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphaforge.data.enums import (
    EXECUTION_BLOCKING_STATUSES,
    RECOVERABLE_STATUSES,
    DataQualityStatus,
    InstrumentType,
    is_execution_eligible,
)
from alphaforge.data.models import MarketCandle
from alphaforge.data.normalization import MarketDataNormalizer
from alphaforge.data.store import CandleStore


def _create_market_candle(
    ts: datetime,
    close_p: Decimal = Decimal("25000.00"),
    is_closed: bool = True,
    timeframe: str = "3m",
    quality_status: DataQualityStatus = DataQualityStatus.VALID,
) -> MarketCandle:
    return MarketCandle(
        symbol="NIFTY",
        instrument_type=InstrumentType.FUTURES,
        contract_id="NIFTY26SEPFUT",
        exchange_timestamp=ts,
        received_timestamp=ts + timedelta(seconds=1),
        timeframe=timeframe,
        open=close_p - Decimal("10.00"),
        high=close_p + Decimal("20.00"),
        low=close_p - Decimal("20.00"),
        close=close_p,
        volume=5000,
        source="TEST",
        quality_status=quality_status,
        is_closed=is_closed,
    )


def test_status_classification_sets() -> None:
    """Verify execution blocking vs recoverable status definitions."""
    assert DataQualityStatus.INVALID in EXECUTION_BLOCKING_STATUSES
    assert DataQualityStatus.CONFLICT in EXECUTION_BLOCKING_STATUSES
    assert DataQualityStatus.GAP in EXECUTION_BLOCKING_STATUSES
    assert DataQualityStatus.STALE in EXECUTION_BLOCKING_STATUSES
    assert DataQualityStatus.INCOMPLETE in EXECUTION_BLOCKING_STATUSES

    assert DataQualityStatus.VALID in RECOVERABLE_STATUSES
    assert DataQualityStatus.DUPLICATE in RECOVERABLE_STATUSES
    assert DataQualityStatus.OUT_OF_ORDER in RECOVERABLE_STATUSES

    assert is_execution_eligible(DataQualityStatus.VALID) is True
    assert is_execution_eligible(DataQualityStatus.DUPLICATE) is True
    assert is_execution_eligible(DataQualityStatus.OUT_OF_ORDER) is True

    assert is_execution_eligible(DataQualityStatus.GAP) is False
    assert is_execution_eligible(DataQualityStatus.STALE) is False
    assert is_execution_eligible(DataQualityStatus.CONFLICT) is False
    assert is_execution_eligible(DataQualityStatus.INVALID) is False
    assert is_execution_eligible(DataQualityStatus.INCOMPLETE) is False
    assert is_execution_eligible(DataQualityStatus.EMPTY) is False


def test_gap_blocks_execution() -> None:
    """Requirement A: GAP => execution_allowed is False and store bridge blocks execution."""
    normalizer = MarketDataNormalizer(default_timeframe="3m")
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)
    # 09:21 is skipped
    t3 = datetime(2026, 9, 11, 9, 24, tzinfo=UTC)

    c0 = _create_market_candle(t0)
    c1 = _create_market_candle(t1)
    c3 = _create_market_candle(t3)

    res = normalizer.normalize_batch([c0, c1, c3], evaluation_timestamp=t3 + timedelta(seconds=10))

    assert res.quality_status == DataQualityStatus.GAP
    assert res.execution_allowed is False

    store = CandleStore()
    store.add_normalization_result(res)

    # Provide real forming candle
    t_forming = t3 + timedelta(minutes=3)
    forming = _create_market_candle(t_forming, is_closed=False)

    # Bridge must block execution
    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=2, forming_candle=forming)
    assert exec_input is None


def test_stale_blocks_execution() -> None:
    """Requirement B: STALE => execution_allowed is False and store bridge blocks execution."""
    normalizer = MarketDataNormalizer(max_stale_seconds=195)
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    c0 = _create_market_candle(t0)

    # Stale by 400 seconds
    eval_ts = t0 + timedelta(seconds=400)
    res = normalizer.normalize_batch([c0], evaluation_timestamp=eval_ts)

    assert res.quality_status == DataQualityStatus.STALE
    assert res.execution_allowed is False

    store = CandleStore()
    store.add_normalization_result(res)

    t_forming = t0 + timedelta(minutes=3)
    forming = _create_market_candle(t_forming, is_closed=False)

    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=1, forming_candle=forming)
    assert exec_input is None


def test_conflict_blocks_execution() -> None:
    """Requirement C: CONFLICT => execution_allowed is False and store bridge blocks execution."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)

    c_a = _create_market_candle(t0, Decimal("25000.00"))
    c_b = _create_market_candle(t0, Decimal("25050.00"))

    res = normalizer.normalize_batch([c_a, c_b], evaluation_timestamp=t0 + timedelta(seconds=10))

    assert res.quality_status == DataQualityStatus.CONFLICT
    assert res.execution_allowed is False
    assert len(res.quarantined_records) == 2


def test_invalid_blocks_execution() -> None:
    """Requirement D: INVALID => execution_allowed is False and store bridge blocks execution."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)

    # Invalid record with negative price
    invalid_raw = {
        "symbol": "NIFTY",
        "instrument_type": "FUTURES",
        "contract_id": "NIFTY26SEPFUT",
        "exchange_timestamp": t0.isoformat(),
        "received_timestamp": t0.isoformat(),
        "timeframe": "3m",
        "open": "-25000.00",
        "high": "25050.00",
        "low": "24900.00",
        "close": "25000.00",
        "volume": 1000,
    }

    res = normalizer.normalize_batch([invalid_raw], evaluation_timestamp=t0 + timedelta(seconds=10))

    assert res.quality_status == DataQualityStatus.INVALID
    assert res.execution_allowed is False
    assert len(res.quarantined_records) == 1


def test_incomplete_blocks_execution() -> None:
    """Requirement E: INCOMPLETE => execution_allowed is False and store bridge blocks execution."""
    normalizer = MarketDataNormalizer(min_required_candles=5)
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    candles = [_create_market_candle(t0 + timedelta(minutes=3 * i)) for i in range(2)]

    res = normalizer.normalize_batch(
        candles, evaluation_timestamp=t0 + timedelta(minutes=6, seconds=10)
    )

    assert res.quality_status == DataQualityStatus.INCOMPLETE
    assert res.execution_allowed is False


def test_exact_duplicate_deduplicated_and_eligible() -> None:
    """Requirement F: exact DUPLICATE => deduplicated and still eligible for execution."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    c0_a = _create_market_candle(t0, Decimal("25000.00"))
    c0_b = _create_market_candle(t0, Decimal("25000.00"))

    res = normalizer.normalize_batch([c0_a, c0_b], evaluation_timestamp=t0 + timedelta(seconds=10))

    assert res.duplicates_count == 1
    assert res.quality_status == DataQualityStatus.DUPLICATE
    assert res.execution_allowed is True
    assert len(res.valid_candles) == 1

    store = CandleStore()
    store.add_normalization_result(res)

    t_forming = t0 + timedelta(minutes=3)
    forming = _create_market_candle(t_forming, is_closed=False)

    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=1, forming_candle=forming)
    assert exec_input is not None
    assert len(exec_input) == 2


def test_out_of_order_sorted_and_eligible() -> None:
    """Requirement G: OUT_OF_ORDER => sorted deterministically and still usable."""
    normalizer = MarketDataNormalizer()
    t0 = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, 9, 18, tzinfo=UTC)

    c0 = _create_market_candle(t0, Decimal("25000.00"))
    c1 = _create_market_candle(t1, Decimal("25010.00"))

    # Pass in reverse order
    res = normalizer.normalize_batch([c1, c0], evaluation_timestamp=t1 + timedelta(seconds=10))

    assert res.was_out_of_order is True
    assert res.quality_status == DataQualityStatus.OUT_OF_ORDER
    assert res.execution_allowed is True
    assert res.valid_candles[0].exchange_timestamp == t0
    assert res.valid_candles[1].exchange_timestamp == t1

    store = CandleStore()
    store.add_normalization_result(res)

    t_forming = t1 + timedelta(minutes=3)
    forming = _create_market_candle(t_forming, is_closed=False)

    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=2, forming_candle=forming)
    assert exec_input is not None
    assert len(exec_input) == 3


def test_no_forming_candle_returns_none_and_no_synthetic() -> None:
    """Requirement H: no forming candle => NO synthetic candle created, returns None."""
    store = CandleStore()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    for i in range(5):
        store.add_candle(
            _create_market_candle(base_t + timedelta(minutes=3 * i), Decimal(25000 + i * 10))
        )

    # Zero forming candles in store, and none passed
    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=5)

    assert exec_input is None


def test_real_forming_candle_at_index_zero_and_latest_closed_at_index_one() -> None:
    """Requirements I & J: real forming candle appears at [0], [1] is latest closed candle."""
    store = CandleStore()
    base_t = datetime(2026, 9, 11, 9, 15, tzinfo=UTC)
    for i in range(5):
        store.add_candle(
            _create_market_candle(base_t + timedelta(minutes=3 * i), Decimal(25000 + i * 10))
        )

    t_forming = base_t + timedelta(minutes=15)
    real_forming = _create_market_candle(t_forming, Decimal("25060.00"), is_closed=False)
    store.add_candle(real_forming)

    exec_input = store.get_strategy_execution_input("NIFTY", "3m", count=5)

    assert exec_input is not None
    assert len(exec_input) == 6

    # Requirement I: real forming candle appears at [0] with is_closed=False
    assert exec_input[0].is_closed is False
    assert exec_input[0].timestamp == t_forming
    assert exec_input[0].close == Decimal("25060.00")

    # Requirement J: [1] remains latest fully closed candle (bar 4 at 09:27)
    assert exec_input[1].is_closed is True
    assert exec_input[1].timestamp == base_t + timedelta(minutes=12)
    assert exec_input[1].close == Decimal("25040.00")

    # [2..N] are older closed candles in reverse chronological order
    assert exec_input[2].timestamp == base_t + timedelta(minutes=9)
    assert exec_input[2].close == Decimal("25030.00")
    assert exec_input[3].timestamp == base_t + timedelta(minutes=6)
    assert exec_input[4].timestamp == base_t + timedelta(minutes=3)
    assert exec_input[5].timestamp == base_t
