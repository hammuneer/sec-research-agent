"""Per-topic retrieval + LLM extraction, run in parallel."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..llm import LLMClient
from ..models import ScoredChunk, TopicResult
from ..reports.prompts import Topic
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

EXTRACTION_MAX_TOKENS = 2048
_RULE = "=" * 50


def format_context(hits: Sequence[ScoredChunk]) -> str:
    """Render retrieved chunks with their provenance header."""
    parts = []
    for i, hit in enumerate(hits, 1):
        m = hit.chunk.metadata
        parts.append(f"[{i}|{m['doc_type']}|{m['period_date']}|{m['filename']}]\n{hit.chunk.text}")
    return "\n\n".join(parts)


class QueryEngine:
    """Retrieves context for each topic and asks the extraction model to summarise it."""

    def __init__(self, store: VectorStore, llm: LLMClient, system_prompt: str) -> None:
        self.store = store
        self.llm = llm
        self.system_prompt = system_prompt

    def extract(self, topic: Topic) -> TopicResult:
        """Run retrieval and extraction for one topic."""
        started = time.perf_counter()
        hits = self.store.multi_query(
            topic.queries,
            top_k=topic.top_k,
            doc_types=[d.value for d in topic.doc_types],
            all_periods=topic.all_periods,
        )
        n_docs = len({h.chunk.metadata["filename"] for h in hits})
        user = (
            f"Topic: {topic.name}\n"
            f"Chunks: {len(hits)} from {n_docs} docs\n"
            f"{_RULE}\n{format_context(hits)}\n{_RULE}\n\n"
            f"{topic.prompt}"
        )
        response = self.llm.chat(self.system_prompt, user, max_tokens=EXTRACTION_MAX_TOKENS)
        return TopicResult(
            topic_id=topic.id,
            response=response,
            n_chunks=len(hits),
            elapsed_seconds=time.perf_counter() - started,
        )

    def extract_all(self, topics: Sequence[Topic], max_workers: int = 5) -> list[TopicResult]:
        """Extract every topic in parallel; failed topics are logged and skipped.

        Results are returned in the order of ``topics``.
        """
        results: dict[str, TopicResult] = {}
        workers = max(1, min(max_workers, len(topics)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.extract, topic): topic for topic in topics}
            for future in as_completed(futures):
                topic = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.error("Topic %r failed: %s", topic.name, exc)
                    continue
                results[topic.id] = result
                logger.info(
                    "Topic %-50s %3d chunks  %5.1fs", topic.name, result.n_chunks, result.elapsed_seconds
                )
        return [results[t.id] for t in topics if t.id in results]
