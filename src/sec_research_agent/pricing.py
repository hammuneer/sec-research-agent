"""Token cost estimation."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from .config import ModelPrice, get_config
from .models import TokenCost, TokenUsage

logger = logging.getLogger(__name__)

# USD per 1M tokens. `config/settings.yaml` -> `pricing` overrides/extends these.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-5": ModelPrice(input=1.25, output=10.0),
    "gpt-5.4": ModelPrice(input=2.50, output=15.0),
    "gpt-4o": ModelPrice(input=2.50, output=10.0),
    "gpt-4o-mini": ModelPrice(input=0.15, output=0.60),
    "text-embedding-3-small": ModelPrice(input=0.02, output=0.0),
    "text-embedding-3-large": ModelPrice(input=0.13, output=0.0),
}

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_warned: set[str] = set()


def _price_table() -> dict[str, ModelPrice]:
    return {**DEFAULT_PRICES, **get_config().pricing}


def lookup_price(model: str, table: Mapping[str, ModelPrice] | None = None) -> ModelPrice | None:
    """Return the price for ``model`` (dated snapshots match their base name), or None."""
    prices = table if table is not None else _price_table()
    return prices.get(model) or prices.get(_DATE_SUFFIX.sub("", model))


def estimate_cost(usage: TokenUsage, table: Mapping[str, ModelPrice] | None = None) -> TokenCost:
    """Estimate USD cost. Unknown models cost 0 and log a single warning."""
    price = lookup_price(usage.model, table)
    if price is None:
        if usage.model not in _warned:
            _warned.add(usage.model)
            logger.warning("No price configured for model %r; reporting cost as 0", usage.model)
        return TokenCost()
    return TokenCost(
        input_cost=usage.input_tokens / 1_000_000 * price.input,
        output_cost=usage.output_tokens / 1_000_000 * price.output,
    )
