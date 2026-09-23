"""Tests for :mod:`sec_research_agent.rag.query_engine`."""

from __future__ import annotations

from types import SimpleNamespace

from sec_research_agent.models import Chunk, LLMResponse, TokenUsage
from sec_research_agent.rag.query_engine import QueryEngine, format_context
from sec_research_agent.rag.vector_store import VectorStore
from sec_research_agent.reports.prompts import Topic


def _topic(topic_id: str, **overrides: object) -> Topic:
    defaults = {
        "id": topic_id,
        "name": topic_id.replace("_", " ").title(),
        "queries": [f"query about {topic_id}"],
        "doc_types": [],
        "prompt": f"Extract {topic_id}",
    }
    defaults.update(overrides)
    return Topic.model_validate(defaults)


class _FakeLLM:
    """Stand-in for :class:`LLMClient` that never touches the network."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    def chat(self, system: str, user: str, max_tokens: int = 8192, temperature: float = 0.3) -> LLMResponse:
        self.calls.append(user)
        for name in self.fail_for:
            if name in user:
                raise RuntimeError(f"simulated failure for {name}")
        return LLMResponse(content=f"extracted: {user[:20]}", usage=TokenUsage(model="fake", input_tokens=5))


def _empty_store() -> VectorStore:
    return VectorStore(client=SimpleNamespace(), embed_model="fake")


def test_format_context_includes_provenance_header() -> None:
    from sec_research_agent.models import ScoredChunk

    chunk = Chunk(
        text="body", metadata={"doc_type": "10-K", "period_date": "2024-01-01", "filename": "a.pdf"}
    )
    rendered = format_context([ScoredChunk(chunk, 0.9)])
    assert "10-K" in rendered and "2024-01-01" in rendered and "a.pdf" in rendered and "body" in rendered


def test_extract_runs_against_empty_store_without_crashing() -> None:
    llm = _FakeLLM()
    engine = QueryEngine(_empty_store(), llm, "system prompt")
    result = engine.extract(_topic("business_model"))
    assert result.n_chunks == 0
    assert result.topic_id == "business_model"
    assert result.response.content.startswith("extracted:")


def test_extract_all_preserves_topic_order() -> None:
    llm = _FakeLLM()
    engine = QueryEngine(_empty_store(), llm, "system prompt")
    topics = [_topic("alpha"), _topic("beta"), _topic("gamma"), _topic("delta")]
    results = engine.extract_all(topics, max_workers=4)
    assert [r.topic_id for r in results] == ["alpha", "beta", "gamma", "delta"]


def test_extract_all_skips_failed_topics_but_keeps_the_rest() -> None:
    llm = _FakeLLM(fail_for={"beta"})
    engine = QueryEngine(_empty_store(), llm, "system prompt")
    topics = [_topic("alpha"), _topic("beta"), _topic("gamma")]
    results = engine.extract_all(topics, max_workers=3)
    assert [r.topic_id for r in results] == ["alpha", "gamma"]


def test_extract_all_returns_empty_list_for_no_topics() -> None:
    llm = _FakeLLM()
    engine = QueryEngine(_empty_store(), llm, "system prompt")
    assert engine.extract_all([], max_workers=5) == []
