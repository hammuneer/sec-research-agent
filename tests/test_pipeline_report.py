"""End-to-end test for :mod:`sec_research_agent.pipeline.report` with a fake OpenAI client.

No network call is made: OpenAI (chat + embeddings) is replaced with :class:`FakeOpenAIClient`
and ``fetch_snapshot`` (Yahoo Finance) is monkeypatched. Source documents are small, real PDFs
written to a temporary store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeOpenAIClient, write_pdf
from sec_research_agent.config import AppConfig
from sec_research_agent.models import DocType, StockSnapshot
from sec_research_agent.pipeline import report as report_module
from sec_research_agent.pipeline.report import ReportError, ReportPipeline
from sec_research_agent.storage import LocalDocumentStore


@pytest.fixture
def chat_only_config() -> AppConfig:
    """All model roles routed through Chat Completions so the fake client stays simple."""
    config = AppConfig()
    config.models.extraction = "gpt-4o-mini"
    config.models.report = "gpt-4o-mini"
    config.models.formatting = "gpt-4o-mini"
    config.models.embedding = "text-embedding-3-small"
    config.concurrency.llm_workers = 2
    config.concurrency.pdf_workers = 2
    config.retrieval.chunk_size = 500
    config.retrieval.chunk_overlap = 50
    return config


@pytest.fixture(autouse=True)
def _no_network_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let the pipeline touch Yahoo Finance."""
    monkeypatch.setattr(
        report_module,
        "fetch_snapshot",
        lambda ticker: StockSnapshot(
            ticker=ticker, company_name=f"{ticker} Inc.", price=100.0, market_cap=1e9
        ),
    )


def _populate_store(store: LocalDocumentStore, ticker: str) -> None:
    write_pdf(
        store.doc_dir(ticker, DocType.TEN_K, create=True) / f"{ticker}_FY_2024_01_01_10-K.pdf",
        "The company generates revenue from selling widgets. " * 40,
    )
    write_pdf(
        store.doc_dir(ticker, DocType.EARNINGS_CALL, create=True)
        / f"{ticker}_FY2024_Q1_2024_02_15_EarningsCall.pdf",
        "Operator: welcome to the call. CEO: revenue grew nicely this quarter. " * 40,
    )


def test_report_pipeline_end_to_end_produces_files(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig
) -> None:
    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)
    _populate_store(store, "AAPL")

    fake_client = FakeOpenAIClient(embed_dim=4)
    pipeline = ReportPipeline(settings, chat_only_config, store=store, openai_client=fake_client)
    result = pipeline.run("AAPL")

    assert result.ticker == "AAPL"
    assert result.files.markdown.exists()
    assert result.files.docx.exists()
    assert result.files.pdf.exists()
    assert result.cost_path.exists()
    assert "total_cost_usd" in result.cost
    assert result.elapsed_seconds >= 0

    # The embeddings client was actually used (documents were chunked and embedded).
    assert fake_client.embeddings.calls
    # The chat client was used both for topic extraction and for report writing/editing.
    assert len(fake_client.chat.completions.calls) >= 2


def test_report_pipeline_raises_when_no_documents(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig
) -> None:
    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)  # empty: no filings collected yet
    pipeline = ReportPipeline(settings, chat_only_config, store=store, openai_client=FakeOpenAIClient())

    with pytest.raises(ReportError, match="No readable documents"):
        pipeline.run("AAPL")


def test_report_pipeline_step_callback_invoked_in_order(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig
) -> None:
    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)
    _populate_store(store, "AAPL")
    pipeline = ReportPipeline(
        settings, chat_only_config, store=store, openai_client=FakeOpenAIClient(embed_dim=4)
    )

    steps: list[str] = []
    pipeline.run("AAPL", on_step=steps.append)
    assert steps[0] == "Reading documents"
    assert "Exporting" in steps
    assert steps.index("Building search index") < steps.index("Fetching market data")


def test_report_pipeline_second_run_reuses_embedding_cache(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second run must hit the on-disk embedding cache instead of re-embedding documents.

    Per-topic retrieval also calls the embeddings API (to embed each topic's queries), so the
    right signal is "was VectorStore.build() invoked", not "was embeddings.create() ever called".
    """
    from sec_research_agent.rag.vector_store import VectorStore

    build_calls = []
    original_build = VectorStore.build

    def counting_build(self, chunks):
        build_calls.append(len(chunks))
        return original_build(self, chunks)

    monkeypatch.setattr(VectorStore, "build", counting_build)

    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)
    _populate_store(store, "AAPL")

    fake_client = FakeOpenAIClient(embed_dim=4)
    pipeline = ReportPipeline(settings, chat_only_config, store=store, openai_client=fake_client)
    pipeline.run("AAPL")
    assert len(build_calls) == 1  # first run: no cache yet, embeddings built from scratch

    pipeline.run("AAPL")
    assert len(build_calls) == 1  # second run: cache hit, build() not called again


def test_report_pipeline_cache_invalidates_when_documents_change(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sec_research_agent.rag.vector_store import VectorStore

    build_calls = []
    original_build = VectorStore.build

    def counting_build(self, chunks):
        build_calls.append(len(chunks))
        return original_build(self, chunks)

    monkeypatch.setattr(VectorStore, "build", counting_build)

    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)
    _populate_store(store, "AAPL")

    fake_client = FakeOpenAIClient(embed_dim=4)
    pipeline = ReportPipeline(settings, chat_only_config, store=store, openai_client=fake_client)
    pipeline.run("AAPL")
    assert len(build_calls) == 1

    # A new filing changes the document fingerprint: the cache key changes too.
    write_pdf(
        store.doc_dir("AAPL", DocType.TEN_Q, create=True) / "AAPL_Q2_2024_06_30_10-Q.pdf",
        "Quarterly update: widget sales accelerated. " * 40,
    )
    pipeline.run("AAPL")
    assert len(build_calls) == 2  # different document set: cache misses, rebuilds


def test_report_pipeline_writes_generated_report_content(
    tmp_path: Path, settings_factory, chat_only_config: AppConfig
) -> None:
    settings = settings_factory(openai_api_key="sk-fake")
    store = LocalDocumentStore(settings.data_dir)
    _populate_store(store, "AAPL")

    def reply(model: str, messages: list[dict]) -> str:
        return "# AAPL Research Report\n\nThis is the fake generated report body."

    fake_client = FakeOpenAIClient(embed_dim=4, chat_reply=reply)
    pipeline = ReportPipeline(settings, chat_only_config, store=store, openai_client=fake_client)
    result = pipeline.run("AAPL")

    content = result.files.markdown.read_text(encoding="utf-8")
    assert "AAPL Research Report" in content
