"""
End-to-end integration test: Market Data Layer to Phase 1 Strategy Engine.
Verifies the complete pipeline:
Raw Market Records -> Normalization Engine -> Candle Store -> Strategy Evaluation.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from alphaforge.core.enums import (
    FuturesConfirmationStatus,
    RejectionCode,
    SignalDirection,
    StrategyDecision,
)
from alphaforge.data.enums import DataQualityStatus
from alphaforge.data.normalization import MarketDataNormalizer
from alphaforge.data.store import CandleStore
from alphaforge.strategy.config import StrategyConfig
from alphaforge.strategy.engine import DeterministicStrategyEngine

FIXTURES_DIR = Path(__file__).parent.parent / "golden" / "fixtures"


def _load_scenario(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        return cast("dict[str, Any]", json.load(f))


def test_end_to_end_raw_to_strategy_valid_long() -> None:
    """
    Test end-to-end:
    1. Load valid long scenario.
    2. Convert candles to raw input format (strings, dicts, out of order, duplicates).
    3. Run MarketDataNormalizer.
    4. Store into CandleStore.
    5. Retrieve strategy inputs via bridge methods.
    6. Evaluate via DeterministicStrategyEngine.
    7. Verify ACCEPT decision matching Phase 1 golden signal.
    """
    fixture = _load_scenario("scenario_01_valid_long.json")

    # Construct raw records for execution timeframe (3m)
    raw_exec_inputs: list[dict[str, Any]] = []
    for c_data in fixture["exec_candles"]:
        ts_str = c_data["timestamp"].replace("+00:00", "Z")
        raw_exec_inputs.append(
            {
                "symbol": "nifty",  # unnormalized lowercase
                "instrument_type": "futures",  # unnormalized lowercase
                "contract_id": "NIFTY26SEPFUT",
                "exchange_timestamp": ts_str,
                "received_timestamp": ts_str,
                "timeframe": "3m",
                "open": str(c_data["open"]),  # string price
                "high": str(c_data["high"]),
                "low": str(c_data["low"]),
                "close": str(c_data["close"]),
                "volume": int(c_data["volume"]),
                "open_interest": c_data["open_interest"],
                "source": "RAW_FEED",
                "is_closed": bool(c_data["is_closed"]),
            }
        )

    # Insert an identical duplicate of a closed candle
    raw_exec_inputs.append(dict(raw_exec_inputs[2]))

    # Shuffle raw_exec_inputs so they arrive out of order
    shuffled_exec = list(reversed(raw_exec_inputs))

    # Normalize 3m execution records
    normalizer_3m = MarketDataNormalizer(default_timeframe="3m", min_required_candles=20)
    norm_result_3m = normalizer_3m.normalize_batch(shuffled_exec)

    assert norm_result_3m.was_out_of_order is True
    assert norm_result_3m.duplicates_count == 1
    assert len(norm_result_3m.quarantined_records) == 0

    # Store execution candles
    store = CandleStore()
    store.add_candles(norm_result_3m.valid_candles)

    # Normalize and store 15m confirmation records
    raw_conf_inputs: list[dict[str, Any]] = []
    for c_data in fixture["conf_candles"]:
        ts_str = c_data["timestamp"].replace("+00:00", "Z")
        raw_conf_inputs.append(
            {
                "symbol": "NIFTY",
                "instrument_type": "INDEX",
                "contract_id": "NIFTY-SPOT",
                "exchange_timestamp": ts_str,
                "received_timestamp": ts_str,
                "timeframe": "15m",
                "open": str(c_data["open"]),
                "high": str(c_data["high"]),
                "low": str(c_data["low"]),
                "close": str(c_data["close"]),
                "volume": int(c_data["volume"]),
                "open_interest": None,
                "source": "SPOT_FEED",
                "is_closed": bool(c_data["is_closed"]),
            }
        )

    normalizer_15m = MarketDataNormalizer(default_timeframe="15m")
    norm_result_15m = normalizer_15m.normalize_batch(raw_conf_inputs, timeframe="15m")
    store.add_candles(norm_result_15m.valid_candles)

    eval_ts = datetime.fromisoformat(fixture["evaluation_timestamp"])
    latest_3m = store.get_latest_candle("NIFTY", "3m")
    assert latest_3m is not None

    # Build strategy inputs via store bridges
    # Count of closed candles is total - 1 (since index [0] is forming)
    closed_count = len(fixture["exec_candles"]) - 1
    raw_exec_bridge = store.get_strategy_execution_input("NIFTY", "3m", count=closed_count)
    assert raw_exec_bridge is not None
    raw_conf_bridge = store.get_strategy_confirmation_input(
        "NIFTY", "15m", max_timestamp=latest_3m.exchange_timestamp
    )

    # Evaluate via Strategy Engine
    cfg = StrategyConfig()
    engine = DeterministicStrategyEngine(config=cfg)

    signal = engine.evaluate(
        raw_exec_candles=raw_exec_bridge,
        raw_conf_candles=raw_conf_bridge,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_ts,
    )

    assert signal.decision == StrategyDecision.ACCEPT
    assert signal.direction == SignalDirection.LONG
    assert signal.rejection_code == RejectionCode.REJECT_NONE
    assert signal.entry_reference == latest_3m.close


def test_end_to_end_stale_data_rejection() -> None:
    """Normalizer detects STALE and strategy engine rejects with REJECT_DATA_STALE."""
    fixture = _load_scenario("scenario_09_stale_data.json")

    raw_exec_inputs = list(
        reversed(
            [
                {
                    "symbol": "NIFTY",
                    "instrument_type": "FUTURES",
                    "contract_id": "NIFTY26SEPFUT",
                    "exchange_timestamp": c["timestamp"],
                    "received_timestamp": c["timestamp"],
                    "timeframe": "3m",
                    "open": str(c["open"]),
                    "high": str(c["high"]),
                    "low": str(c["low"]),
                    "close": str(c["close"]),
                    "volume": int(c["volume"]),
                    "open_interest": c["open_interest"],
                    "source": "FEED",
                    "is_closed": bool(c["is_closed"]),
                }
                for c in fixture["exec_candles"]
            ]
        )
    )

    eval_ts = datetime.fromisoformat(fixture["evaluation_timestamp"])
    normalizer = MarketDataNormalizer(max_stale_seconds=195)
    res_3m = normalizer.normalize_batch(raw_exec_inputs, evaluation_timestamp=eval_ts)

    # Data is 30 mins old (> 195s), so normalizer flags STALE and execution is blocked
    assert res_3m.quality_status == DataQualityStatus.STALE
    assert res_3m.execution_allowed is False

    store = CandleStore()
    store.add_normalization_result(res_3m)

    closed_count = len(fixture["exec_candles"]) - 1
    # Store fail-closed gate: execution input is blocked (returns None)
    exec_candles = store.get_strategy_execution_input("NIFTY", "3m", count=closed_count)
    assert exec_candles is None

    # Verify Phase 1 engine defense-in-depth: if stale candles are directly passed,
    # the engine still rejects with REJECT_DATA_STALE
    raw_conf_inputs = [
        {
            "symbol": "NIFTY",
            "instrument_type": "INDEX",
            "contract_id": "NIFTY-SPOT",
            "exchange_timestamp": c["timestamp"],
            "received_timestamp": c["timestamp"],
            "timeframe": "15m",
            "open": str(c["open"]),
            "high": str(c["high"]),
            "low": str(c["low"]),
            "close": str(c["close"]),
            "volume": int(c["volume"]),
            "open_interest": None,
            "source": "FEED",
            "is_closed": bool(c["is_closed"]),
        }
        for c in fixture["conf_candles"]
    ]
    res_15m = normalizer.normalize_batch(raw_conf_inputs, timeframe="15m")
    store.add_candles(res_15m.valid_candles)

    latest_3m = store.get_latest_candle("NIFTY", "3m")
    assert latest_3m is not None
    conf_candles = store.get_strategy_confirmation_input(
        "NIFTY", "15m", max_timestamp=latest_3m.exchange_timestamp
    )

    engine = DeterministicStrategyEngine()
    # Pass directly formatted raw candles to engine
    direct_exec = [c.to_strategy_candle() for c in res_3m.valid_candles]
    signal = engine.evaluate(
        raw_exec_candles=direct_exec,
        raw_conf_candles=conf_candles,
        futures_status=FuturesConfirmationStatus.CONFIRMED,
        evaluation_timestamp=eval_ts,
    )

    assert signal.decision == StrategyDecision.REJECT
    assert signal.rejection_code == RejectionCode.REJECT_DATA_STALE
