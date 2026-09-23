"""Tests for :mod:`sec_research_agent.rag.vector_store` (no network; OpenAI client is faked)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sec_research_agent.models import Chunk
from sec_research_agent.rag.vector_store import (
    CACHE_VERSION,
    EMBED_BATCH_SIZE,
    VectorStore,
    recency_score,
)

_IDX = re.compile(r"IDX_(\d+)")


class _OneHotEmbeddings:
    """Embeds each text as a one-hot vector at the index encoded in its text (``IDX_<n>``).

    This makes it possible to verify, after :meth:`VectorStore.build`, that every embedding
    row lines up with the *global* chunk it belongs to rather than a batch-local position -
    the exact scenario the old "reused loop variable" bug got wrong across batches.
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def create(self, model: str, input: list[str]):
        data = []
        for local_idx, text in enumerate(input):
            global_idx = int(_IDX.search(text).group(1))
            vector = [0.0] * self.dim
            vector[global_idx] = 1.0
            data.append(SimpleNamespace(embedding=vector, index=local_idx))
        usage = SimpleNamespace(prompt_tokens=len(input), total_tokens=len(input))
        return SimpleNamespace(data=data, usage=usage)


class _OneHotClient:
    def __init__(self, dim: int) -> None:
        self.embeddings = _OneHotEmbeddings(dim)


def _make_chunk(global_idx: int) -> Chunk:
    return Chunk(
        text=f"IDX_{global_idx} some body text describing chunk number {global_idx}.",
        metadata={
            "filename": f"doc_{global_idx}.pdf",
            "doc_type": "10-K",
            "period_date": "2024-01-01",
            "chunk_idx": 0,
        },
    )


def test_build_pairs_embeddings_with_the_correct_chunk_across_batches() -> None:
    """Regression test: batch 2+ must not reuse batch 1's embeddings (old `reused i` bug)."""
    n = EMBED_BATCH_SIZE + 10  # forces exactly 2 batches
    chunks = [_make_chunk(i) for i in range(n)]
    store = VectorStore(_OneHotClient(n), embed_model="fake-embed")
    store.build(chunks)

    assert len(store) == n
    assert store.embeddings.shape == (n, n)
    for i in range(n):
        # Row i must be (near-)one-hot at position i: the embedding belongs to chunk i.
        row = store.embeddings[i]
        assert np.argmax(row) == i, f"chunk {i} paired with the wrong embedding"
        assert row[i] == pytest.approx(1.0, abs=1e-5)


def test_build_accumulates_usage_across_batches() -> None:
    n = EMBED_BATCH_SIZE + 10
    chunks = [_make_chunk(i) for i in range(n)]
    store = VectorStore(_OneHotClient(n), embed_model="fake-embed")
    store.build(chunks)
    assert store.usage.input_tokens == n
    assert store.usage.total_tokens == n


def test_build_rejects_empty_chunk_list() -> None:
    store = VectorStore(_OneHotClient(4), embed_model="fake-embed")
    with pytest.raises(ValueError, match="zero chunks"):
        store.build([])


def test_build_raises_if_api_returns_wrong_vector_count() -> None:
    class _ShortEmbeddings:
        def create(self, model: str, input: list[str]):
            data = [SimpleNamespace(embedding=[0.0, 0.0], index=0)]  # too few
            return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=1, total_tokens=1))

    class _ShortClient:
        embeddings = _ShortEmbeddings()

    store = VectorStore(_ShortClient(), embed_model="fake-embed")
    with pytest.raises(RuntimeError, match="Embedding API returned"):
        store.build([_make_chunk(0), _make_chunk(1)])


# --------------------------------------------------------------------------- recency


def test_recency_score_today_is_near_one() -> None:
    today = datetime(2025, 6, 1)
    assert recency_score("2025-06-01", now=today) == pytest.approx(1.0)


def test_recency_score_decays_and_floors() -> None:
    now = datetime(2025, 6, 1)
    ten_years_ago = "2015-06-01"
    assert recency_score(ten_years_ago, now=now) == pytest.approx(0.5)  # floored


def test_recency_score_unknown_date_is_neutral() -> None:
    assert recency_score("") == 0.7
    assert recency_score("not-a-date") == 0.7


# --------------------------------------------------------------------------- query


def _chunk(filename: str, doc_type: str) -> Chunk:
    return Chunk(
        text=filename[0],
        metadata={"filename": filename, "doc_type": doc_type, "period_date": "2024-01-01", "chunk_idx": 0},
    )


def _dim4_client(vectors: dict[str, list[float]]) -> SimpleNamespace:
    class _Embeddings:
        def create(self, model: str, input: list[str]):
            data = [SimpleNamespace(embedding=vectors[t], index=i) for i, t in enumerate(input)]
            usage = SimpleNamespace(prompt_tokens=len(input), total_tokens=len(input))
            return SimpleNamespace(data=data, usage=usage)

    return SimpleNamespace(embeddings=_Embeddings())


def test_multi_query_returns_empty_for_empty_store() -> None:
    store = VectorStore(_dim4_client({}), embed_model="fake")
    assert store.multi_query(["anything"]) == []


def test_multi_query_filters_by_doc_type() -> None:
    chunks = [_chunk("a.pdf", "10-K"), _chunk("b.pdf", "10-Q")]
    vectors = {
        "[10-K | 2024-01-01 | a.pdf]\na": [1.0, 0.0],
        "[10-Q | 2024-01-01 | b.pdf]\nb": [1.0, 0.0],
        "q": [1.0, 0.0],
    }
    store = VectorStore(_dim4_client(vectors), embed_model="fake")
    store.build(chunks)
    hits = store.multi_query(["q"], top_k=5, doc_types=["10-K"])
    assert {h.chunk.metadata["filename"] for h in hits} == {"a.pdf"}


def test_multi_query_deduplicates_across_queries() -> None:
    chunks = [_chunk("a.pdf", "10-K")]
    vectors = {
        "[10-K | 2024-01-01 | a.pdf]\na": [1.0, 0.0],
        "q1": [1.0, 0.0],
        "q2": [0.9, 0.1],
    }
    store = VectorStore(_dim4_client(vectors), embed_model="fake")
    store.build(chunks)
    hits = store.multi_query(["q1", "q2"], top_k=5)
    assert len(hits) == 1  # same chunk hit by both queries, de-duplicated


# --------------------------------------------------------------------------- cache


def test_cache_round_trip(tmp_path: Path) -> None:
    chunks = [_make_chunk(0), _make_chunk(1), _make_chunk(2)]
    store = VectorStore(_OneHotClient(3), embed_model="fake-embed")
    store.build(chunks)
    cache_path = tmp_path / "cache" / "AAPL_abc123"
    store.save(cache_path)

    assert cache_path.with_suffix(".npz").exists()
    assert cache_path.with_suffix(".json").exists()

    loaded = VectorStore(_OneHotClient(3), embed_model="fake-embed")
    assert loaded.load(cache_path) is True
    assert len(loaded) == 3
    np.testing.assert_array_equal(loaded.embeddings, store.embeddings)
    assert loaded.usage.input_tokens == store.usage.input_tokens


def test_cache_miss_when_files_absent(tmp_path: Path) -> None:
    store = VectorStore(_OneHotClient(3), embed_model="fake-embed")
    assert store.load(tmp_path / "nothing_here") is False


def test_cache_invalidates_on_version_mismatch(tmp_path: Path) -> None:
    chunks = [_make_chunk(0)]
    store = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    store.build(chunks)
    cache_path = tmp_path / "cache"
    store.save(cache_path)

    meta = json.loads(cache_path.with_suffix(".json").read_text(encoding="utf-8"))
    meta["version"] = CACHE_VERSION + 1
    cache_path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    fresh = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    assert fresh.load(cache_path) is False


def test_cache_invalidates_on_embed_model_mismatch(tmp_path: Path) -> None:
    chunks = [_make_chunk(0)]
    store = VectorStore(_OneHotClient(1), embed_model="model-a")
    store.build(chunks)
    cache_path = tmp_path / "cache"
    store.save(cache_path)

    fresh = VectorStore(_OneHotClient(1), embed_model="model-b")
    assert fresh.load(cache_path) is False


def test_cache_invalidates_when_documents_change(tmp_path: Path) -> None:
    """A cache built for one chunk set must not silently apply to a different one.

    The report pipeline keys the cache path itself on a fingerprint of the source documents,
    so a changed document set writes to (and reads from) a different path; here we confirm the
    lower-level guarantee the cache format itself offers: chunk/embedding counts must match.
    """
    store = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    store.build([_make_chunk(0)])
    cache_path = tmp_path / "cache"
    store.save(cache_path)

    # Corrupt the on-disk chunk list length so it no longer matches the embedding matrix.
    meta = json.loads(cache_path.with_suffix(".json").read_text(encoding="utf-8"))
    meta["chunks"] = meta["chunks"] * 2
    cache_path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    fresh = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    assert fresh.load(cache_path) is False


def test_cache_ignores_corrupt_json(tmp_path: Path) -> None:
    store = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    store.build([_make_chunk(0)])
    cache_path = tmp_path / "cache"
    store.save(cache_path)
    cache_path.with_suffix(".json").write_text("{not valid json", encoding="utf-8")

    fresh = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    assert fresh.load(cache_path) is False


def test_cache_load_handles_truncated_npz_gracefully(tmp_path: Path) -> None:
    """A concurrent writer could leave a truncated ``.npz`` mid-save; loading it must not crash.

    ``np.load`` can raise a variety of exception types for a corrupt/truncated archive (not just
    ``OSError``/``ValueError``, e.g. ``zipfile.BadZipFile``), so this is a regression test for
    broadening the caught exceptions in :meth:`VectorStore.load`.
    """
    store = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    store.build([_make_chunk(0)])
    cache_path = tmp_path / "cache"
    store.save(cache_path)

    cache_path.with_suffix(".npz").write_bytes(b"not a real npz file, truncated mid-write")

    fresh = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    assert fresh.load(cache_path) is False


def test_save_writes_cache_files_atomically(tmp_path: Path) -> None:
    """``save`` must leave no partial temp files behind and use :func:`storage.write_atomic`."""
    import inspect

    from sec_research_agent.rag import vector_store as vs_module

    source = inspect.getsource(vs_module.VectorStore.save)
    assert "write_atomic" in source

    store = VectorStore(_OneHotClient(1), embed_model="fake-embed")
    store.build([_make_chunk(0)])
    cache_path = tmp_path / "cache"
    store.save(cache_path)

    leftovers = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob(".*tmp*"))
    assert leftovers == []


def test_cache_does_not_use_pickle(tmp_path: Path) -> None:
    """The cache is npz + json; `np.load` must be called with allow_pickle=False (source check)."""
    import inspect

    from sec_research_agent.rag import vector_store as vs_module

    source = inspect.getsource(vs_module.VectorStore.load)
    assert "allow_pickle=False" in source
    assert "pickle.load" not in source
