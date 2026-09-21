"""
Unit tests for AlphaForge Fundamentals Domain & Scoring Engine.
Verifies mathematical scoring invariants, dynamic price filtering,
continuous rank ordering, and leaderboard aggregates.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from alphaforge.fundamentals import (
    COMPILED_UNIVERSE,
    calculate_fundamental_scores,
    get_available_sectors,
    get_stock_by_symbol,
    get_top_stocks,
)
from alphaforge.fundamentals.models import (
    FundamentalUniverseResponse,
    HealthRating,
    StockFundamental,
    VerdictType,
)
from scripts.dashboard_server import AlphaForgeRequestHandler


def test_compiled_universe_size_and_diversity():
    """Universe must contain at least 50 market leaders across diverse sectors."""
    assert len(COMPILED_UNIVERSE) >= 50
    sectors = get_available_sectors()
    assert len(sectors) >= 7
    assert "IT & Software" in sectors
    assert "Banking & Finance" in sectors
    assert "FMCG" in sectors


def test_stock_fundamental_schema_and_pillar_invariants():
    """All stocks must satisfy mathematical bounds and pillar score sums."""
    for s in COMPILED_UNIVERSE:
        assert isinstance(s, StockFundamental)
        assert 0 <= s.score <= 100
        assert 0 <= s.profitability_score <= 30
        assert 0 <= s.solvency_score <= 25
        assert 0 <= s.valuation_score <= 25
        assert 0 <= s.growth_score <= 20
        # Invariant: Score equals sum of all 4 pillars
        expected_sum = (
            s.profitability_score
            + s.solvency_score
            + s.valuation_score
            + s.growth_score
        )
        assert s.score == expected_sum
        assert s.cmp > 0
        assert s.pe_ratio > 0
        assert s.debt_to_equity >= 0.0
        assert len(s.business_summary) > 10
        assert len(s.strengths) >= 2


def test_calculate_fundamental_scores_deterministic():
    """Deterministic validation of scoring logic with boundary inputs."""
    p, s, v, g, total, verdict, health = calculate_fundamental_scores(
        roe=30.0,
        roce=40.0,
        debt_to_equity=0.0,
        pe_ratio=12.0,
        dividend_yield=3.5,
        profit_growth_3y=25.0,
        sales_growth_3y=20.0,
        sector="IT & Software",
    )
    # 15+15=30 p, 25 s, 15+10=25 v, 10+10=20 g => 100
    assert p == 30
    assert s == 25
    assert v == 25
    assert g == 20
    assert total == 100
    assert verdict == VerdictType.STRONG_BUY
    assert health == HealthRating.EXCELLENT


def test_get_top_stocks_default_limit_and_ranking():
    """Default screen must return Top 20 stocks ordered by score descending."""
    res = get_top_stocks()
    assert isinstance(res, FundamentalUniverseResponse)
    assert len(res.stocks) == 20
    assert res.filtered_count == len(COMPILED_UNIVERSE)

    # Ranks must be continuous 1 to 20
    for idx, stock in enumerate(res.stocks, start=1):
        assert stock.rank == idx

    # Scores must be strictly monotonically non-increasing
    scores = [s.score for s in res.stocks]
    assert scores == sorted(scores, reverse=True)


def test_price_range_filtering():
    """Stocks returned must strictly satisfy min_price <= CMP <= max_price."""
    min_p = 500.0
    max_p = 1500.0
    res = get_top_stocks(min_price=min_p, max_price=max_p, limit=20)
    assert len(res.stocks) > 0
    assert len(res.stocks) <= 20

    for s in res.stocks:
        assert min_p <= s.cmp <= max_p, f"{s.symbol} cmp {s.cmp} out of [{min_p}, {max_p}]"

    # Ranks must start at 1 and be contiguous
    ranks = [s.rank for s in res.stocks]
    assert ranks == list(range(1, len(res.stocks) + 1))


def test_price_range_under_500():
    """Under 500 bucket must filter budget stocks correctly."""
    res = get_top_stocks(min_price=0.0, max_price=500.0, limit=20)
    assert len(res.stocks) >= 5
    for s in res.stocks:
        assert s.cmp <= 500.0


def test_price_range_over_3000():
    """Above 3000 bucket must include ultra high price stocks like MRF, Page, Bosch, Maruti."""
    res = get_top_stocks(min_price=3000.0, max_price=1_000_000.0, limit=20)
    assert len(res.stocks) == 20
    for s in res.stocks:
        assert s.cmp >= 3000.0


def test_sector_and_text_search_filtering():
    """Filtering by sector and search query must be case-insensitive and accurate."""
    it_res = get_top_stocks(sector="IT & Software")
    assert len(it_res.stocks) > 0
    for s in it_res.stocks:
        assert s.sector == "IT & Software"

    tata_res = get_top_stocks(query="Tata")
    assert len(tata_res.stocks) >= 3
    for s in tata_res.stocks:
        assert "tata" in s.name.lower() or "tata" in s.symbol.lower()


def test_leaderboard_highlights():
    """Leaderboard cards must accurately highlight top performers."""
    res = get_top_stocks()
    lb = res.leaderboard
    assert lb.top_ranked is not None
    assert lb.top_ranked.rank == 1
    assert lb.best_value is not None
    assert lb.safest_debt_free is not None
    assert lb.growth_leader is not None

    # Safest debt free must have 0.0 D/E
    assert lb.safest_debt_free.debt_to_equity == 0.0


def test_get_stock_by_symbol_found_and_not_found():
    """Lookup by ticker symbol."""
    tcs = get_stock_by_symbol("TCS")
    assert tcs is not None
    assert tcs.symbol == "TCS"
    assert tcs.cmp == 4250.0

    missing = get_stock_by_symbol("NON_EXISTENT_TICKER")
    assert missing is None


# =====================================================================
# Dashboard HTTP API Endpoint Integration Tests
# =====================================================================


@pytest.fixture(scope="module")
def api_server_url():
    server = HTTPServer(("127.0.0.1", 0), AlphaForgeRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_api_fundamentals_top20_endpoint(api_server_url: str):
    """GET /api/fundamentals/top20 returns 200 with complete Top 20 payload."""
    url = f"{api_server_url}/api/fundamentals/top20"
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "stocks" in data
        assert len(data["stocks"]) == 20
        assert data["total_universe_count"] >= 50
        assert "leaderboard" in data
        assert data["leaderboard"]["top_ranked"]["symbol"] == data["stocks"][0]["symbol"]


def test_api_fundamentals_top20_with_price_filter(api_server_url: str):
    """GET /api/fundamentals/top20?min_price=500&max_price=1500 returns filtered stocks."""
    url = f"{api_server_url}/api/fundamentals/top20?min_price=500&max_price=1500"
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        stocks = data["stocks"]
        assert len(stocks) > 0
        for s in stocks:
            assert 500.0 <= s["cmp"] <= 1500.0


def test_api_fundamentals_stock_endpoint(api_server_url: str):
    """GET /api/fundamentals/stock?symbol=TCS returns individual stock details."""
    url = f"{api_server_url}/api/fundamentals/stock?symbol=TCS"
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["symbol"] == "TCS"
        assert data["roe"] == 51.2
        assert "strengths" in data
        assert len(data["strengths"]) > 0
