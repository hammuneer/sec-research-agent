"""Turn topic extractions into a written report, then polish it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..llm import LLMClient
from ..models import LLMResponse, StockSnapshot, TopicResult
from .prompts import Topic, report_generation_instructions

REPORT_MAX_TOKENS = 8192
REPORT_TEMPERATURE = 0.25
_RULE = "=" * 60


def build_report_request(
    ticker: str,
    stock: StockSnapshot,
    topic_results: Sequence[TopicResult],
    topics: Sequence[Topic],
    doc_filenames: Sequence[str],
    today: datetime | None = None,
) -> str:
    """User message for the report-writing model."""
    topics_by_id = {t.id: t for t in topics}
    sections = []
    for result in topic_results:
        topic = topics_by_id.get(result.topic_id)
        if topic is None:
            continue
        feeds = ", ".join(topic.stencil_sections)
        sections.append(f"\n{_RULE}\n{topic.name.upper()} (for: {feeds})\n{_RULE}\n{result.response.content}")
    doc_list = "\n".join(f"- {name}" for name in sorted(doc_filenames))
    date_str = (today or datetime.now()).strftime("%B %d, %Y")
    return (
        "Generate the report for:\n\n"
        f"Ticker: {ticker}\n"
        f"Company Name: {stock.company_name}\n"
        f"Exchange: {stock.exchange}\n"
        f"Current Stock Price (Yahoo Finance): {stock.price_str}\n"
        f"Current Market Cap (Yahoo Finance): {stock.market_cap_str}\n"
        f"Sector: {stock.sector}\n"
        f"Industry: {stock.industry}\n"
        f"Date: {date_str}\n\n"
        "CURATED DATA (from permitted sources only):\n"
        f"{''.join(sections)}\n\n"
        f"{_RULE}\nDOCUMENTS (for Primary Source Disclosure):\n{doc_list}\n{_RULE}\n\n"
        f"{report_generation_instructions()}"
    )


class ReportWriter:
    """Runs the two LLM passes: draft the report, then rewrite it for style."""

    def __init__(self, writer: LLMClient, editor: LLMClient) -> None:
        self.writer = writer
        self.editor = editor

    def draft(self, system_prompt: str, request: str) -> LLMResponse:
        """First pass: write the report from curated data."""
        return self.writer.chat(
            system_prompt, request, max_tokens=REPORT_MAX_TOKENS, temperature=REPORT_TEMPERATURE
        )

    def polish(self, system_prompt: str, report: str) -> LLMResponse:
        """Second pass: editorial rewrite without changing facts."""
        user = f"Here is the report you need to re-write: \n\n### REPORT\n{report}"
        return self.editor.chat(
            system_prompt, user, max_tokens=REPORT_MAX_TOKENS, temperature=REPORT_TEMPERATURE
        )
