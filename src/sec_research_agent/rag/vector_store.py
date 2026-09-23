"""In-memory vector index with recency boosting and a safe on-disk cache (npz + json)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

import numpy as np
from openai import OpenAI

from ..models import Chunk, ScoredChunk, TokenUsage
from ..storage import write_atomic

logger = logging.getLogger(__name__)

CACHE_VERSION = 2
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
# OpenAI allows 300K tokens per embeddings request; ~1K tokens per chunk keeps 250 well below it.
EMBED_BATCH_SIZE = 250
MIN_PER_YEAR = 3  # stratified retrieval: best chunks guaranteed per fiscal year


def recency_score(period_date: str, now: datetime | None = None) -> float:
    """1.0 for today, decaying 8% per year, floored at 0.5. Unknown dates score 0.7."""
    try:
        d = datetime.strptime(period_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.7
    years = ((now or datetime.now()) - d).days / 365.25
    return max(0.5, 1.0 - years * 0.08)


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return cast(np.ndarray, matrix / np.maximum(norms, 1e-10))


class VectorStore:
    """Cosine-similarity index over document chunks."""

    def __init__(self, client: OpenAI, embed_model: str = DEFAULT_EMBED_MODEL) -> None:
        self.client = client
        self.embed_model = embed_model
        self.chunks: list[Chunk] = []
        self.embeddings: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.recency: np.ndarray = np.zeros(0, dtype=np.float32)
        self.usage = TokenUsage(model=embed_model)

    def __len__(self) -> int:
        return len(self.chunks)

    # ------------------------------------------------------------------ build

    def _embed(self, texts: Sequence[str]) -> tuple[np.ndarray, TokenUsage]:
        resp = self.client.embeddings.create(model=self.embed_model, input=list(texts))
        vectors = np.array([d.embedding for d in sorted(resp.data, key=lambda d: d.index)], dtype=np.float32)
        usage = TokenUsage(
            model=self.embed_model,
            input_tokens=resp.usage.prompt_tokens,
            total_tokens=resp.usage.total_tokens,
        )
        return vectors, usage

    def build(self, chunks: Sequence[Chunk]) -> None:
        """Embed ``chunks`` in batches and index them."""
        if not chunks:
            raise ValueError("Cannot build an index from zero chunks")
        vectors: list[np.ndarray] = []
        usage = TokenUsage(model=self.embed_model)
        n_batches = (len(chunks) + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
        for batch_no, offset in enumerate(range(0, len(chunks), EMBED_BATCH_SIZE), start=1):
            batch = chunks[offset : offset + EMBED_BATCH_SIZE]
            batch_vectors, batch_usage = self._embed([c.embed_text for c in batch])
            if len(batch_vectors) != len(batch):
                raise RuntimeError(
                    f"Embedding API returned {len(batch_vectors)} vectors for {len(batch)} inputs"
                )
            vectors.append(batch_vectors)
            usage = usage + batch_usage
            logger.debug("Embedded batch %d/%d (%d chunks)", batch_no, n_batches, len(batch))

        self.chunks = list(chunks)
        self.embeddings = _normalize(np.vstack(vectors))
        self.recency = np.array([recency_score(c.metadata["period_date"]) for c in chunks], dtype=np.float32)
        self.usage = usage

    # ------------------------------------------------------------------ query

    def _rank(
        self, query_vector: np.ndarray, top_k: int, boost: float, doc_types: Sequence[str] | None
    ) -> list[ScoredChunk]:
        scores = (1 - boost) * (self.embeddings @ query_vector) + boost * self.recency
        if doc_types:
            allowed = set(doc_types)
            mask = np.array([c.metadata["doc_type"] in allowed for c in self.chunks])
            scores = np.where(mask, scores, -1.0)
        top = np.argsort(scores)[::-1][:top_k]
        return [ScoredChunk(self.chunks[i], float(scores[i])) for i in top if scores[i] > 0]

    def multi_query(
        self,
        queries: Sequence[str],
        top_k: int = 15,
        doc_types: Sequence[str] | None = None,
        all_periods: bool = False,
        boost: float = 0.15,
    ) -> list[ScoredChunk]:
        """Run several queries, de-duplicate, and return up to ``2 * top_k`` best chunks.

        With ``all_periods`` the selection is stratified: each fiscal year contributes its best
        ``MIN_PER_YEAR`` chunks first, then the remaining budget goes to the best overall.
        """
        if not self.chunks:
            return []
        query_vectors, _ = self._embed(queries)
        query_vectors = _normalize(query_vectors)

        best: dict[tuple[str, int], ScoredChunk] = {}
        for vector in query_vectors:
            for hit in self._rank(vector, top_k, boost, doc_types):
                if hit.key not in best or hit.score > best[hit.key].score:
                    best[hit.key] = hit
        combined = sorted(best.values(), key=lambda h: h.score, reverse=True)
        budget = top_k * 2
        if not all_periods:
            return combined[:budget]
        return _stratify_by_year(combined, budget)

    # ------------------------------------------------------------------ cache

    def save(self, path: Path) -> None:
        """Persist to ``<path>.npz`` + ``<path>.json`` (no pickle), each written atomically.

        Atomic (temp file + rename) writes mean a reader never sees a half-written file, even
        if two report runs for the same ticker save to this path concurrently - the result is
        always one writer's complete pair of files, never a corrupt mix of the two.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, embeddings=self.embeddings, recency=self.recency)
        write_atomic(path.with_suffix(".npz"), buffer.getvalue())
        meta = {
            "version": CACHE_VERSION,
            "embed_model": self.embed_model,
            "usage": self.usage.model_dump(),
            "chunks": [c.model_dump() for c in self.chunks],
        }
        write_atomic(path.with_suffix(".json"), json.dumps(meta).encode("utf-8"))

    def load(self, path: Path) -> bool:
        """Load a cache written by :meth:`save`. Returns False if missing, stale or corrupt.

        Any failure while reading falls back to a cache miss (the index is rebuilt) rather than
        raising - including a truncated ``.npz`` from a write that was still in flight, which
        ``numpy`` can report as a variety of exception types, not just ``OSError``/``ValueError``.
        """
        npz_path, json_path = path.with_suffix(".npz"), path.with_suffix(".json")
        if not (npz_path.exists() and json_path.exists()):
            return False
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            if meta.get("version") != CACHE_VERSION or meta.get("embed_model") != self.embed_model:
                return False
            with np.load(npz_path, allow_pickle=False) as arrays:
                embeddings, recency = arrays["embeddings"], arrays["recency"]
            chunks = [Chunk.model_validate(c) for c in meta["chunks"]]
        except Exception as exc:
            logger.warning("Ignoring unreadable embedding cache %s: %s", path, exc)
            return False
        if len(chunks) != len(embeddings):
            return False
        self.chunks, self.embeddings, self.recency = chunks, embeddings, recency
        self.usage = TokenUsage.model_validate(meta["usage"])
        return True


def _stratify_by_year(ranked: list[ScoredChunk], budget: int) -> list[ScoredChunk]:
    """Guarantee coverage of every year before filling ``budget`` with the best remaining hits."""
    by_year: dict[str, list[ScoredChunk]] = {}
    for hit in ranked:
        year = (hit.chunk.metadata.get("period_date") or "")[:4] or "unknown"
        by_year.setdefault(year, []).append(hit)

    selected: dict[tuple[str, int], ScoredChunk] = {}
    for year in sorted(by_year):
        for hit in by_year[year][:MIN_PER_YEAR]:
            selected.setdefault(hit.key, hit)
    for hit in ranked:
        if len(selected) >= budget:
            break
        selected.setdefault(hit.key, hit)
    return sorted(selected.values(), key=lambda h: h.score, reverse=True)[:budget]
