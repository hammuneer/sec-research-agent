"""Shared data models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocType(StrEnum):
    """Source document types. The value is the label used in prompts and topic filters."""

    TEN_K = "10-K"
    TEN_Q = "10-Q"
    PROXY = "Proxy"
    EARNINGS_CALL = "EarningsCall"

    @property
    def folder(self) -> str:
        """Sub-folder name under ``data/<TICKER>/``."""
        return "EarningsCalls" if self is DocType.EARNINGS_CALL else self.value

    @property
    def display_name(self) -> str:
        """Human-readable label."""
        return _DISPLAY_NAMES[self]


_DISPLAY_NAMES = {
    DocType.TEN_K: "Annual reports (10-K)",
    DocType.TEN_Q: "Quarterly reports (10-Q)",
    DocType.PROXY: "Proxy statements (DEF 14A)",
    DocType.EARNINGS_CALL: "Earnings call transcripts",
}


# ---------------------------------------------------------------------------
# LLM usage
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token counts for one or more API calls against a single model."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            model=self.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    def __str__(self) -> str:
        return f"{self.model}: in={self.input_tokens:,} out={self.output_tokens:,}"


class TokenCost(BaseModel):
    """USD cost for a :class:`TokenUsage`."""

    input_cost: float = 0.0
    output_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        """Input plus output cost."""
        return self.input_cost + self.output_cost


class LLMResponse(BaseModel):
    """Text returned by a model together with its usage."""

    content: str
    usage: TokenUsage


# ---------------------------------------------------------------------------
# Documents and retrieval
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """A source PDF on disk plus its extracted text."""

    path: Path
    doc_type: DocType
    period_date: str  # ISO date (YYYY-MM-DD) or "" when unknown
    fiscal_year: str | None = None
    quarter: str | None = None
    text: str = ""
    page_count: int = 0

    @property
    def filename(self) -> str:
        """File name without directory."""
        return self.path.name

    @property
    def char_count(self) -> int:
        """Number of extracted characters."""
        return len(self.text)


class Chunk(BaseModel):
    """A slice of a document's text with provenance metadata."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    text: str
    metadata: dict[str, Any]

    @property
    def embed_text(self) -> str:
        """Text sent to the embedding model (metadata header + body)."""
        m = self.metadata
        return f"[{m['doc_type']} | {m['period_date']} | {m['filename']}]\n{self.text}"


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieved chunk and its blended similarity/recency score."""

    chunk: Chunk
    score: float

    @property
    def key(self) -> tuple[str, int]:
        """Stable identity used for de-duplication."""
        return self.chunk.metadata["filename"], self.chunk.metadata["chunk_idx"]


class TopicResult(BaseModel):
    """LLM extraction for one report topic."""

    topic_id: str
    response: LLMResponse
    n_chunks: int
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


@dataclass
class StockSnapshot:
    """Price and profile data from Yahoo Finance."""

    ticker: str
    company_name: str = ""
    price: float | None = None
    market_cap: float | None = None
    sector: str = "N/A"
    industry: str = "N/A"
    exchange: str = "N/A"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def price_str(self) -> str:
        """Formatted price, e.g. ``$123.45``."""
        return f"${self.price:,.2f}" if self.price else "N/A"

    @property
    def market_cap_str(self) -> str:
        """Formatted market cap, e.g. ``$1.23T``."""
        mc = self.market_cap
        if not mc:
            return "N/A"
        for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if mc >= threshold:
                return f"${mc / threshold:.2f}{suffix}"
        return f"${mc:,.0f}"
