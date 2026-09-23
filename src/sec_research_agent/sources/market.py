"""Market data snapshot via Yahoo Finance."""

from __future__ import annotations

import logging

import yfinance as yf

from ..models import StockSnapshot

logger = logging.getLogger(__name__)


def fetch_snapshot(ticker: str) -> StockSnapshot:
    """Current price, market cap and profile for ``ticker``. Never raises; missing fields stay N/A."""
    snapshot = StockSnapshot(ticker=ticker, company_name=ticker)
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:  # yfinance raises a wide variety of errors
        logger.warning("[%s] Yahoo Finance lookup failed: %s", ticker, exc)
        return snapshot
    snapshot.price = info.get("currentPrice") or info.get("regularMarketPrice")
    snapshot.market_cap = info.get("marketCap")
    snapshot.sector = info.get("sector") or "N/A"
    snapshot.industry = info.get("industry") or "N/A"
    snapshot.exchange = info.get("exchange") or "N/A"
    snapshot.company_name = info.get("longName") or info.get("shortName") or ticker
    return snapshot
