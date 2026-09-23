"""Split documents into overlapping chunks, preferring paragraph boundaries."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Chunk, Document

DEFAULT_CHUNK_SIZE = 4000  # characters, roughly 1,000 tokens
DEFAULT_CHUNK_OVERLAP = 400


def chunk_document(
    doc: Document, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[Chunk]:
    """Chunk one document. Breaks at the last blank line in the second half of a window."""
    if overlap >= size:
        raise ValueError("chunk overlap must be smaller than chunk size")
    text = doc.text
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            chunks.append(
                Chunk(
                    text=piece,
                    metadata={
                        "filename": doc.filename,
                        "doc_type": doc.doc_type.value,
                        "period_date": doc.period_date,
                        "chunk_idx": len(chunks),
                    },
                )
            )
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_documents(
    documents: Iterable[Document], size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[Chunk]:
    """Chunk every document and concatenate the results."""
    return [chunk for doc in documents for chunk in chunk_document(doc, size, overlap)]
