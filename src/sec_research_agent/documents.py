"""Discover stored PDFs for a company and extract their text."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pypdf import PdfReader

from .models import DocType, Document
from .storage import LocalDocumentStore

logger = logging.getLogger(__name__)

_DATE = re.compile(r"(?<!\d)(\d{4})_(\d{2})_(\d{2})(?!\d)")
_FISCAL_YEAR = re.compile(r"_FY(\d{4})_")
_QUARTER = re.compile(r"_Q([1-4])_")
_LEGACY_YEAR_QUARTER = re.compile(r"_(\d{4})_Q([1-4])_")
_QUARTER_END = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}


def parse_period_date(filename: str) -> str:
    """ISO date embedded in a stored filename (report period or call date), or ``""``."""
    match = _DATE.search(filename)
    if match:
        return "-".join(match.groups())
    legacy = _LEGACY_YEAR_QUARTER.search(filename)
    if legacy:
        year, quarter = legacy.groups()
        return f"{year}-{_QUARTER_END[quarter]}"
    return ""


def build_document(path: Path, doc_type: DocType) -> Document:
    """Create a :class:`Document` from a stored file; type comes from its folder."""
    name = path.name
    period_date = parse_period_date(name)
    fiscal_year = _FISCAL_YEAR.search(name)
    quarter = _QUARTER.search(name)
    return Document(
        path=path,
        doc_type=doc_type,
        period_date=period_date,
        fiscal_year=fiscal_year.group(1) if fiscal_year else (period_date[:4] or None),
        quarter=f"Q{quarter.group(1)}" if quarter else None,
    )


def discover_documents(store: LocalDocumentStore, ticker: str) -> list[Document]:
    """All source documents for ``ticker``. Generated reports are never included."""
    documents = [
        build_document(path, doc_type)
        for doc_type, paths in store.all_documents(ticker).items()
        for path in paths
    ]
    return sorted(documents, key=lambda d: (d.doc_type.value, d.filename))


def extract_pdf_text(path: Path) -> tuple[str, int]:
    """Return ``(text, page_count)`` for a PDF. Unreadable pages are skipped."""
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf raises many parser-specific errors
            logger.debug("%s: page extraction failed: %s", path.name, exc)
            continue
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages), len(reader.pages)


def extract_all(documents: list[Document], workers: int = 8) -> list[Document]:
    """Fill ``text`` / ``page_count`` in place (in parallel); returns documents that yielded text."""

    def _extract(doc: Document) -> Document:
        try:
            doc.text, doc.page_count = extract_pdf_text(doc.path)
        except Exception as exc:
            logger.error("Could not read %s: %s", doc.filename, exc)
        return doc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(_extract, documents))
    readable = [d for d in results if d.text]
    if len(readable) < len(results):
        logger.warning("%d of %d documents produced no text", len(results) - len(readable), len(results))
    return readable
