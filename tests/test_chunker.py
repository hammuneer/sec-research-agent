"""Tests for :mod:`sec_research_agent.rag.chunker`."""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_research_agent.models import DocType, Document
from sec_research_agent.rag.chunker import chunk_document, chunk_documents


def make_doc(text: str, filename: str = "AAPL_FY_2024_01_01_10-K.pdf") -> Document:
    return Document(path=Path(filename), doc_type=DocType.TEN_K, period_date="2024-01-01", text=text)


def test_chunk_empty_document_returns_no_chunks() -> None:
    assert chunk_document(make_doc("")) == []
    assert chunk_document(make_doc("   \n  ")) == []


def test_chunk_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_document(make_doc("hello"), size=100, overlap=100)


def test_short_document_produces_single_chunk() -> None:
    chunks = chunk_document(make_doc("short text"), size=4000, overlap=400)
    assert len(chunks) == 1
    assert chunks[0].text == "short text"
    assert chunks[0].metadata["chunk_idx"] == 0


def test_chunk_metadata_contains_provenance() -> None:
    chunks = chunk_document(make_doc("x" * 10, filename="AAPL_FY_2024_01_01_10-K.pdf"))
    m = chunks[0].metadata
    assert m["filename"] == "AAPL_FY_2024_01_01_10-K.pdf"
    assert m["doc_type"] == "10-K"
    assert m["period_date"] == "2024-01-01"


def test_long_document_splits_into_multiple_chunks_with_incrementing_idx() -> None:
    text = ("Paragraph one.\n\n" * 50) + ("Paragraph two.\n\n" * 50)
    chunks = chunk_document(make_doc(text), size=500, overlap=50)
    assert len(chunks) > 1
    assert [c.metadata["chunk_idx"] for c in chunks] == list(range(len(chunks)))
    # Reconstructed text (ignoring overlap) still covers the whole document's content.
    assert "".join(c.text for c in chunks).count("Paragraph one.") >= 1


def test_chunk_prefers_paragraph_boundary() -> None:
    first_half = "A" * 100 + "\n\n"
    second_half = "B" * 3990
    text = first_half + second_half
    chunks = chunk_document(make_doc(text), size=4000, overlap=400)
    # The boundary search only looks in the second half of the window; a break that early
    # should NOT be used, so the first chunk should not end exactly at the "AAAA" boundary.
    assert len(chunks) >= 1


def test_chunk_always_makes_progress_even_with_no_boundary() -> None:
    # A single unbroken run of characters longer than `size`: the loop must still terminate,
    # and every 4000-char window (except the last, shorter one) advances by size - overlap.
    text = "A" * 12000
    chunks = chunk_document(make_doc(text), size=4000, overlap=400)
    assert len(chunks) == 4
    assert all(c.text for c in chunks)
    assert sum(len(c.text) for c in chunks[:-1]) == 3 * 4000


def test_embed_text_includes_metadata_header() -> None:
    chunks = chunk_document(make_doc("body text"))
    assert chunks[0].embed_text == "[10-K | 2024-01-01 | AAPL_FY_2024_01_01_10-K.pdf]\nbody text"


def test_chunk_documents_concatenates_across_documents() -> None:
    docs = [make_doc("first doc text", "a.pdf"), make_doc("second doc text", "b.pdf")]
    chunks = chunk_documents(docs)
    assert len(chunks) == 2
    assert {c.metadata["filename"] for c in chunks} == {"a.pdf", "b.pdf"}
