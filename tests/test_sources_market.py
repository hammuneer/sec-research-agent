"""Tests for :mod:`sec_research_agent.sources.market` (no network; yfinance is monkeypatched)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sec_research_agent.sources import market as market_module
from sec_research_agent.sources.market import fetch_snapshot


def test_fetch_snapshot_populates_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {
        "currentPrice": 123.45,
        "marketCap": 2_500_000_000_000,
        "sector": "Technology",
        "industry": "Semiconductors",
        "exchange": "NASDAQ",
        "longName": "Example Corp",
    }
    monkeypatch.setattr(market_module.yf, "Ticker", lambda ticker: SimpleNamespace(info=info))
    snapshot = fetch_snapshot("EXMP")
    assert snapshot.price == 123.45
    assert snapshot.market_cap == 2_500_000_000_000
    assert snapshot.sector == "Technology"
    assert snapshot.company_name == "Example Corp"
    assert snapshot.price_str == "$123.45"
    assert snapshot.market_cap_str == "$2.50T"


def test_fetch_snapshot_never_raises_on_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ticker: str):
        raise ConnectionError("no network in tests")

    monkeypatch.setattr(market_module.yf, "Ticker", boom)
    snapshot = fetch_snapshot("EXMP")
    assert snapshot.ticker == "EXMP"
    assert snapshot.price is None
    assert snapshot.price_str == "N/A"
    assert snapshot.market_cap_str == "N/A"


def test_fetch_snapshot_handles_missing_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_module.yf, "Ticker", lambda ticker: SimpleNamespace(info=None))
    snapshot = fetch_snapshot("EXMP")
    assert snapshot.company_name == "EXMP"
    assert snapshot.sector == "N/A"
