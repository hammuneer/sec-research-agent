"""Tests for :mod:`sec_research_agent.pricing`."""

from __future__ import annotations

import logging

import pytest

from sec_research_agent.config import ModelPrice
from sec_research_agent.models import TokenUsage
from sec_research_agent.pricing import estimate_cost, lookup_price


def test_lookup_price_known_model() -> None:
    table = {"gpt-4o-mini": ModelPrice(input=0.15, output=0.60)}
    price = lookup_price("gpt-4o-mini", table)
    assert price is not None
    assert price.input == 0.15


def test_lookup_price_strips_dated_snapshot_suffix() -> None:
    table = {"gpt-4o-mini": ModelPrice(input=0.15, output=0.60)}
    price = lookup_price("gpt-4o-mini-2024-07-18", table)
    assert price is not None
    assert price.input == 0.15


def test_lookup_price_unknown_model_returns_none() -> None:
    assert lookup_price("totally-unknown-model-xyz", {}) is None


def test_estimate_cost_known_model() -> None:
    usage = TokenUsage(model="gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    table = {"gpt-4o-mini": ModelPrice(input=0.15, output=0.60)}
    cost = estimate_cost(usage, table)
    assert cost.input_cost == 0.15
    assert cost.output_cost == 0.60
    assert cost.total_cost == 0.75


def test_estimate_cost_unknown_model_is_zero_not_an_exception(caplog: pytest.LogCaptureFixture) -> None:
    usage = TokenUsage(model="brand-new-unpriced-model-42", input_tokens=1000, output_tokens=1000)
    with caplog.at_level(logging.WARNING):
        cost = estimate_cost(usage, {})
    assert cost.total_cost == 0.0
    assert cost.input_cost == 0.0
    assert cost.output_cost == 0.0
    assert any("brand-new-unpriced-model-42" in r.message for r in caplog.records)


def test_estimate_cost_unknown_model_warns_only_once(caplog: pytest.LogCaptureFixture) -> None:
    usage = TokenUsage(model="another-unpriced-model-99", input_tokens=100, output_tokens=100)
    with caplog.at_level(logging.WARNING):
        estimate_cost(usage, {})
        estimate_cost(usage, {})
    warnings = [r for r in caplog.records if "another-unpriced-model-99" in r.message]
    assert len(warnings) == 1


def test_estimate_cost_zero_usage_is_free() -> None:
    usage = TokenUsage(model="gpt-4o-mini", input_tokens=0, output_tokens=0)
    table = {"gpt-4o-mini": ModelPrice(input=0.15, output=0.60)}
    assert estimate_cost(usage, table).total_cost == 0.0


def test_price_table_merges_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from sec_research_agent import pricing as pricing_module
    from sec_research_agent.config import AppConfig

    fake_config = AppConfig()
    fake_config.pricing = {"gpt-4o-mini": ModelPrice(input=99.0, output=99.0)}
    monkeypatch.setattr(pricing_module, "get_config", lambda: fake_config)

    table = pricing_module._price_table()
    # Config overrides the built-in default price.
    assert table["gpt-4o-mini"].input == 99.0
    # Defaults not overridden are still present.
    assert "gpt-5" in table
