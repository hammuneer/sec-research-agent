"""Local, company-by-company document storage.

Layout::

    <data_dir>/
      <TICKER>/
        10-K/            annual reports
        10-Q/            quarterly reports
        Proxy/           DEF 14A proxy statements
        EarningsCalls/   earnings call transcripts
        reports/         generated research reports (md / docx / pdf / cost json)
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .models import DocType

TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
REPORTS_FOLDER = "reports"


class InvalidTickerError(ValueError):
    """Raised for ticker symbols that are not safe to use in paths or queries."""


def normalize_ticker(raw: str) -> str:
    """Upper-case and validate a ticker symbol.

    Raises:
        InvalidTickerError: if the symbol is not 1-10 chars of ``A-Z``, ``0-9``, ``.`` or ``-``
            starting with a letter.
    """
    ticker = raw.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file + rename so readers never see partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class LocalDocumentStore:
    """Filesystem store rooted at ``root`` with one folder per company."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def company_dir(self, ticker: str) -> Path:
        """Folder for ``ticker`` (not created)."""
        return self.root / normalize_ticker(ticker)

    def doc_dir(self, ticker: str, doc_type: DocType, *, create: bool = False) -> Path:
        """Folder for one document type of ``ticker``."""
        path = self.company_dir(ticker) / doc_type.folder
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def reports_dir(self, ticker: str, *, create: bool = False) -> Path:
        """Folder for generated reports of ``ticker``."""
        path = self.company_dir(ticker) / REPORTS_FOLDER
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def existing_filenames(self, ticker: str, doc_type: DocType) -> set[str]:
        """Names of PDFs already stored for ``ticker`` / ``doc_type``."""
        return {p.name for p in self.list_documents(ticker, doc_type)}

    def list_documents(self, ticker: str, doc_type: DocType) -> list[Path]:
        """PDFs stored for ``ticker`` / ``doc_type``, newest name first."""
        folder = self.doc_dir(ticker, doc_type)
        if not folder.is_dir():
            return []
        return sorted((p for p in folder.glob("*.pdf") if p.is_file()), reverse=True)

    def all_documents(self, ticker: str) -> dict[DocType, list[Path]]:
        """All source PDFs for ``ticker`` grouped by type (reports excluded)."""
        return {doc_type: self.list_documents(ticker, doc_type) for doc_type in DocType}

    def list_reports(self, ticker: str) -> list[Path]:
        """Generated report files for ``ticker``, newest first."""
        folder = self.reports_dir(ticker)
        if not folder.is_dir():
            return []
        files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def list_companies(self) -> list[str]:
        """Tickers that have a folder under the data root."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and TICKER_PATTERN.fullmatch(p.name))

    def save_document(self, ticker: str, doc_type: DocType, filename: str, data: bytes) -> Path:
        """Persist a downloaded PDF and return its path."""
        if Path(filename).name != filename:
            raise ValueError(f"Filename must not contain path separators: {filename!r}")
        path = self.doc_dir(ticker, doc_type, create=True) / filename
        write_atomic(path, data)
        return path
