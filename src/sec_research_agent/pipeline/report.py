"""Research report pipeline: documents -> embeddings -> topic extraction -> report -> export."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..config import AppConfig, Settings
from ..documents import discover_documents, extract_all
from ..llm import LLMClient, create_openai_client
from ..models import Document, TokenUsage
from ..pricing import estimate_cost
from ..rag.chunker import chunk_documents
from ..rag.query_engine import QueryEngine
from ..rag.vector_store import VectorStore
from ..reports.exporter import ExportedReport, export_report
from ..reports.generator import ReportWriter, build_report_request
from ..reports.prompt_store import PromptStore
from ..reports.prompts import extraction_prompt, load_topics
from ..sources.market import fetch_snapshot
from ..storage import LocalDocumentStore

logger = logging.getLogger(__name__)

StepCallback = Callable[[str], None]


def _noop(message: str) -> None:
    """Default step callback."""


class ReportError(RuntimeError):
    """The report could not be produced."""


@dataclass(frozen=True)
class ReportResult:
    """Files produced for one report and what it cost."""

    ticker: str
    files: ExportedReport
    cost_path: Path
    cost: dict[str, Any]
    elapsed_seconds: float


def _usage_row(usage: TokenUsage) -> dict[str, Any]:
    return {"usage": usage.model_dump(), "cost": round(estimate_cost(usage).total_cost, 6)}


def _sum_usage(usages: list[TokenUsage], model: str) -> TokenUsage:
    total = TokenUsage(model=model)
    for usage in usages:
        total = total + usage
    return total


def _cache_key(
    ticker: str, documents: list[Document], embed_model: str, chunk_size: int, overlap: int
) -> str:
    fingerprint = "|".join(f"{d.filename}:{d.char_count}" for d in documents)
    fingerprint += f"|{embed_model}|{chunk_size}|{overlap}"
    return f"{ticker}_{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"


class ReportPipeline:
    """Builds a research report for a ticker from documents already in the local store."""

    def __init__(
        self,
        settings: Settings,
        config: AppConfig,
        store: LocalDocumentStore | None = None,
        openai_client: OpenAI | None = None,
    ) -> None:
        self.settings = settings
        self.config = config
        self.store = store or LocalDocumentStore(settings.data_dir)
        api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        self.client = openai_client or create_openai_client(api_key)
        models = config.models
        self.extract_llm = LLMClient(self.client, models.extraction)
        self.writer = ReportWriter(
            LLMClient(self.client, models.report, models.reasoning_effort),
            LLMClient(self.client, models.formatting, models.reasoning_effort),
        )
        self.prompt_store = PromptStore(settings.prompt_store_path)

    def run(self, ticker: str, on_step: StepCallback = _noop) -> ReportResult:
        """Generate, export and cost a report for ``ticker``."""
        started = time.perf_counter()
        cfg = self.config

        on_step("Reading documents")
        documents = extract_all(discover_documents(self.store, ticker), workers=cfg.concurrency.pdf_workers)
        if not documents:
            raise ReportError(f"No readable documents for {ticker}; collect filings first")
        logger.info(
            "[%s] %d documents, %s pages, %s chars",
            ticker,
            len(documents),
            f"{sum(d.page_count for d in documents):,}",
            f"{sum(d.char_count for d in documents):,}",
        )

        on_step("Building search index")
        index = self._build_index(ticker, documents)

        on_step("Fetching market data")
        stock = fetch_snapshot(ticker)

        topics = load_topics()
        on_step(f"Extracting {len(topics)} research topics")
        engine = QueryEngine(index, self.extract_llm, extraction_prompt())
        topic_results = engine.extract_all(topics, max_workers=cfg.concurrency.llm_workers)
        if not topic_results:
            raise ReportError("Every topic extraction failed; see logs")

        prompts = self.prompt_store.active()
        on_step("Writing report")
        request = build_report_request(ticker, stock, topic_results, topics, [d.filename for d in documents])
        draft = self.writer.draft(prompts.report_generation, request)

        on_step("Editing report")
        final = self.writer.polish(prompts.report_format, draft.content)

        on_step("Exporting")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{ticker}_report_{stamp}"
        out_dir = self.store.reports_dir(ticker, create=True)
        files = export_report(
            final.content,
            out_dir,
            base,
            brand_name=cfg.report.brand_name,
            letterhead_image=cfg.report.letterhead_image,
        )

        extraction_usage = _sum_usage([r.response.usage for r in topic_results], cfg.models.extraction)
        cost = {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "topics_extracted": f"{len(topic_results)}/{len(topics)}",
            "embeddings": _usage_row(index.usage),
            "topic_extraction": _usage_row(extraction_usage),
            "report": _usage_row(draft.usage),
            "formatting": _usage_row(final.usage),
        }
        cost["total_cost_usd"] = round(sum(v["cost"] for v in cost.values() if isinstance(v, dict)), 6)
        cost_path = out_dir / f"{base}_cost.json"
        cost_path.write_text(json.dumps(cost, indent=2), encoding="utf-8")

        elapsed = time.perf_counter() - started
        logger.info(
            "[%s] Report ready in %.0fs (est. $%.2f): %s", ticker, elapsed, cost["total_cost_usd"], files.pdf
        )
        return ReportResult(ticker, files, cost_path, cost, elapsed)

    def _build_index(self, ticker: str, documents: list[Document]) -> VectorStore:
        cfg = self.config
        index = VectorStore(self.client, cfg.models.embedding)
        key = _cache_key(
            ticker, documents, cfg.models.embedding, cfg.retrieval.chunk_size, cfg.retrieval.chunk_overlap
        )
        cache_path = self.settings.cache_dir / "embeddings" / key
        if cfg.retrieval.use_cache and index.load(cache_path):
            logger.info("[%s] Loaded %d chunks from cache", ticker, len(index))
            # Cached embeddings cost nothing this run.
            index.usage = TokenUsage(model=cfg.models.embedding)
            return index

        chunks = chunk_documents(documents, cfg.retrieval.chunk_size, cfg.retrieval.chunk_overlap)
        logger.info("[%s] Embedding %d chunks with %s", ticker, len(chunks), cfg.models.embedding)
        index.build(chunks)
        if cfg.retrieval.use_cache:
            index.save(cache_path)
        return index
