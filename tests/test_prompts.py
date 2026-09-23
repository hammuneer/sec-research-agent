"""Tests for :mod:`sec_research_agent.reports.prompts`."""

from __future__ import annotations

from sec_research_agent.models import DocType
from sec_research_agent.reports.prompts import default_prompts, load_topics


def test_load_topics_returns_ten_topics() -> None:
    """The report pipeline documents 10 topics; keep the count in sync with topics.yaml."""
    topics = load_topics()
    assert len(topics) == 10


def test_topic_ids_are_unique() -> None:
    topics = load_topics()
    ids = [t.id for t in topics]
    assert len(ids) == len(set(ids))


def test_governance_compensation_topic_exists_with_proxy_doc_type() -> None:
    """The governance topic must retrieve Proxy (and may also reference 10-K) content."""
    topics = {t.id: t for t in load_topics()}
    governance = topics["governance_compensation"]
    assert DocType.PROXY in governance.doc_types
    assert all(isinstance(dt, DocType) for dt in governance.doc_types)


def test_governance_compensation_stencil_section_matches_report_heading() -> None:
    """stencil_sections must point at a heading that actually exists in report_generation.md."""
    topics = {t.id: t for t in load_topics()}
    governance = topics["governance_compensation"]
    report_generation = default_prompts().report_generation
    assert governance.stencil_sections
    for section in governance.stencil_sections:
        assert section in report_generation


def test_report_generation_prompt_references_current_topic_count() -> None:
    """The system prompt tells the model how many topics to expect; keep it accurate."""
    report_generation = default_prompts().report_generation
    assert f"from {len(load_topics())} topics" in report_generation
