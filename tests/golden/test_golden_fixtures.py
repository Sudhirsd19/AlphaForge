"""
Golden fixture test suite for AlphaForge Strategy Specification.
Loads immutable static JSON test fixtures from disk (tests/golden/fixtures/*.json).
Covers all 14 mandatory scenario cases defined in Section 22:
1. valid long
2. valid short
3. trend rejection
4. breakout rejection
5. volume rejection
6. momentum rejection
7. volatility rejection
8. futures rejection
9. stale data
10. invalid data
11. expired setup
12. invalid stop
13. invalid target
14. duplicate signal
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    StrategyDecision,
)
from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.core.models import Candle
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict[str, Any]:
    file_path = FIXTURES_DIR / filename
    with file_path.open(encoding="utf-8") as f:
        return cast("dict[str, Any]", json.load(f))


def _deserialize_candle(d: dict[str, Any]) -> Candle:
    return Candle(
        timestamp=datetime.fromisoformat(d["timestamp"]),
        open=Decimal(d["open"]),
        high=Decimal(d["high"]),
        low=Decimal(d["low"]),
        close=Decimal(d["close"]),
        volume=int(d["volume"]),
        open_interest=d["open_interest"],
        is_closed=bool(d["is_closed"]),
    )


def _build_strategy_config(cfg_dict: dict[str, Any] | None) -> StrategyConfig:
    if not cfg_dict:
        return StrategyConfig()
    kwargs: dict[str, Any] = {
        k: Decimal(v) if isinstance(v, str) else v for k, v in cfg_dict.items()
    }
    cfg_inst = cast("Any", StrategyConfig)(**kwargs)
    return cast("StrategyConfig", cfg_inst)


def test_golden_scenario_01_valid_long() -> None:
    """Fixture 1: Valid Long setup meeting all criteria produces ACCEPT."""
    data = _load_fixture("scenario_01_valid_long.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    exp = data["expected"]

    assert signal.decision.value == exp["decision"]
    assert signal.rejection_code.value == exp["rejection_code"]
    assert signal.direction.value == exp["direction"]
    assert signal.entry_reference == Decimal(exp["entry_reference"])
    assert signal.stop_reference == Decimal(exp["stop_reference"])
    assert signal.target_reference == Decimal(exp["target_reference"])
    assert signal.risk_distance == Decimal(exp["risk_distance"])
    assert signal.stop_reference < signal.entry_reference
    assert signal.target_reference > signal.entry_reference


def test_golden_scenario_02_valid_short() -> None:
    """Fixture 2: Valid Short setup meeting all criteria produces ACCEPT."""
    data = _load_fixture("scenario_02_valid_short.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    exp = data["expected"]

    assert signal.decision.value == exp["decision"]
    assert signal.rejection_code.value == exp["rejection_code"]
    assert signal.direction.value == exp["direction"]
    assert signal.entry_reference == Decimal(exp["entry_reference"])
    assert signal.stop_reference == Decimal(exp["stop_reference"])
    assert signal.target_reference == Decimal(exp["target_reference"])
    assert signal.risk_distance == Decimal(exp["risk_distance"])
    assert signal.stop_reference > signal.entry_reference
    assert signal.target_reference < signal.entry_reference


def test_golden_scenario_03_trend_rejection() -> None:
    """Fixture 3: Neutral higher timeframe trend returns REJECT_TREND."""
    data = _load_fixture("scenario_03_trend_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_04_breakout_rejection() -> None:
    """Fixture 4: Trigger candle inside range returns REJECT_BREAKOUT."""
    data = _load_fixture("scenario_04_breakout_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_05_volume_rejection() -> None:
    """Fixture 5: Volume below 1.20x moving average returns REJECT_VOLUME."""
    data = _load_fixture("scenario_05_volume_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_06_momentum_rejection() -> None:
    """Fixture 6: RSI out of confirmed bounds returns REJECT_MOMENTUM."""
    data = _load_fixture("scenario_06_momentum_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    cfg = _build_strategy_config(data.get("config"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_07_volatility_rejection() -> None:
    """Fixture 7: ATR below minimum bounds returns REJECT_VOLATILITY."""
    data = _load_fixture("scenario_07_volatility_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    cfg = _build_strategy_config(data.get("config"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_08_futures_rejection() -> None:
    """Fixture 8: Futures confirmation status NOT_CONFIRMED returns REJECT_FUTURES_CONFIRMATION."""
    data = _load_fixture("scenario_08_futures_rejection.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_09_stale_data() -> None:
    """Fixture 9: Stale data returns REJECT_DATA_STALE."""
    data = _load_fixture("scenario_09_stale_data.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_10_invalid_data() -> None:
    """Fixture 10: Invalid OHLC invariant raises DataIntegrityError."""
    data = _load_fixture("scenario_10_invalid_data.json")
    inv = data["invalid_candle"]
    with pytest.raises(DataIntegrityError):
        Candle(
            timestamp=datetime.fromisoformat(inv["timestamp"]),
            open=Decimal(inv["open"]),
            high=Decimal(inv["high"]),
            low=Decimal(inv["low"]),
            close=Decimal(inv["close"]),
            volume=int(inv["volume"]),
            open_interest=inv["open_interest"],
            is_closed=bool(inv["is_closed"]),
        )


def test_golden_scenario_11_expired_setup() -> None:
    """Fixture 11: Setup evaluated past maximum window is flagged."""
    data = _load_fixture("scenario_11_expired_setup.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision in (StrategyDecision.REJECT, StrategyDecision.EXPIRED)
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_12_invalid_stop() -> None:
    """Fixture 12: Risk distance below minimum threshold returns REJECT_INVALID_STOP."""
    data = _load_fixture("scenario_12_invalid_stop.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    cfg = _build_strategy_config(data.get("config"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_13_invalid_target() -> None:
    """Fixture 13: Target multiple <= 0 produces REJECT_INVALID_TARGET."""
    data = _load_fixture("scenario_13_invalid_target.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    cfg = _build_strategy_config(data.get("config"))
    engine = DeterministicStrategyEngine(cfg)
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    signal = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert signal.decision.value == data["expected"]["decision"]
    assert signal.rejection_code.value == data["expected"]["rejection_code"]


def test_golden_scenario_14_duplicate_signal() -> None:
    """Fixture 14: Re-evaluating the identical bar setup emits DUPLICATE."""
    data = _load_fixture("scenario_14_duplicate_signal.json")
    exec_c = [_deserialize_candle(c) for c in data["exec_candles"]]
    conf_c = [_deserialize_candle(c) for c in data["conf_candles"]]
    engine = DeterministicStrategyEngine()
    eval_time = datetime.fromisoformat(data["evaluation_timestamp"])
    status = FuturesConfirmationStatus(data["futures_status"])

    sig1 = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert sig1.decision.value == data["expected_first"]["decision"]
    assert sig1.rejection_code.value == data["expected_first"]["rejection_code"]

    sig2 = engine.evaluate(exec_c, conf_c, status, eval_time)
    assert sig2.decision.value == data["expected_second"]["decision"]
    assert sig2.rejection_code.value == data["expected_second"]["rejection_code"]
