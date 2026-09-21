# ruff: noqa: E501, I001
"""
AlphaForge Fundamental Domain Models.
Defines immutable Pydantic schemas for stock fundamental metrics,
ratios, pillar score breakdowns, and API response payloads.
"""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class VerdictType(StrEnum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    ACCUMULATE = "ACCUMULATE"
    FAIR_VALUE = "FAIR VALUE"
    HOLD = "HOLD"


class HealthRating(StrEnum):
    EXCELLENT = "EXCELLENT"
    ROBUST = "ROBUST"
    MODERATE = "MODERATE"


class StockFundamental(BaseModel):
    """Authoritative fundamental record for an institutional Indian stock."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(..., description="NSE Ticker Symbol, e.g. TCS, INFY")
    name: str = Field(..., description="Full Registered Company Name")
    sector: str = Field(..., description="Industry Sector")
    exchange: str = Field(default="NSE", description="Primary Exchange")
    cmp: float = Field(..., description="Current Market Price in INR")
    market_cap_cr: float = Field(..., description="Market Capitalization in Crores INR")
    high_52w: float = Field(..., description="52-Week High in INR")
    low_52w: float = Field(..., description="52-Week Low in INR")

    # Valuation & Multiples
    pe_ratio: float = Field(..., description="Price to Earnings (TTM)")
    industry_pe: float = Field(..., description="Average Sector P/E")
    pb_ratio: float = Field(..., description="Price to Book Value")
    ev_ebitda: float = Field(..., description="Enterprise Value to EBITDA")
    dividend_yield: float = Field(..., description="Annual Dividend Yield %")

    # Capital Efficiency & Profitability
    roe: float = Field(..., description="Return on Equity %")
    roce: float = Field(..., description="Return on Capital Employed %")
    operating_margin: float = Field(..., description="Operating Profit Margin %")

    # Balance Sheet & Solvency
    debt_to_equity: float = Field(..., description="Debt to Equity Ratio (0.00 for debt-free)")
    interest_coverage: float = Field(..., description="Interest Coverage Ratio")
    current_ratio: float = Field(..., description="Current Ratio")
    solvency_status: str = Field(default="DEBT FREE", description="Solvency Classification")

    # Growth Track Record
    sales_growth_3y: float = Field(..., description="3-Year Revenue CAGR %")
    profit_growth_3y: float = Field(..., description="3-Year Net Profit CAGR %")

    # Shareholding Pattern
    promoter_holding: float = Field(..., description="Promoter Holding %")
    promoter_pledge: float = Field(default=0.0, description="Promoter Pledge %")
    institutional_holding: float = Field(..., description="Institutional (FII+DII) Holding %")
    public_holding: float = Field(..., description="Retail / Public Holding %")

    # Quantitative Scoring & Rankings
    score: int = Field(..., ge=0, le=100, description="Composite Fundamental Score (0-100)")
    rank: int = Field(default=1, ge=1, description="Dynamic Rank in Active Screen")
    verdict: VerdictType = Field(default=VerdictType.BUY, description="Quantitative Verdict")
    health_rating: HealthRating = Field(default=HealthRating.ROBUST, description="Balance Sheet Health")

    # 4-Pillar Score Breakdown
    profitability_score: int = Field(..., ge=0, le=30, description="Profitability & Returns (Max 30)")
    solvency_score: int = Field(..., ge=0, le=25, description="Solvency & Debt Health (Max 25)")
    valuation_score: int = Field(..., ge=0, le=25, description="Valuation Attractiveness (Max 25)")
    growth_score: int = Field(..., ge=0, le=20, description="Growth Consistency (Max 20)")

    # Deep-Dive Qualitative Insights (for Detailed Stock Modal)
    business_summary: str = Field(..., description="Institutional company profile and moat summary")
    strengths: list[str] = Field(default_factory=list, description="Key competitive strengths")
    considerations: list[str] = Field(default_factory=list, description="Key risks and monitorables")
    moat_rating: str = Field(default="WIDE MOAT", description="Economic Moat Rating")


class LeaderboardSummary(BaseModel):
    """Highlight cards for the top tier stocks in the active filter."""

    model_config = ConfigDict(frozen=True)

    top_ranked: StockFundamental | None = None
    best_value: StockFundamental | None = None
    safest_debt_free: StockFundamental | None = None
    growth_leader: StockFundamental | None = None


class FundamentalUniverseResponse(BaseModel):
    """Full API payload returned for the Fundamental Screener."""

    model_config = ConfigDict(frozen=True)

    total_universe_count: int
    filtered_count: int
    top_limit: int = 20
    min_price_filter: float
    max_price_filter: float
    sector_filter: str
    search_query: str
    stocks: list[StockFundamental]
    leaderboard: LeaderboardSummary
    sectors_available: list[str]
