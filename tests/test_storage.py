"""Tests for :mod:`sec_research_agent.storage`."""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_research_agent.models import DocType
from sec_research_agent.storage import (
    InvalidTickerError,
    LocalDocumentStore,
    normalize_ticker,
    write_atomic,
)


@pytest.mark.parametrize("raw", ["aapl", "AAPL", "brk.b", "BF-B", "a", "A1"])
def test_normalize_ticker_accepts_valid_symbols(raw: str) -> None:
    assert normalize_ticker(raw) == raw.strip().upper()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "1AAPL",  # must start with a letter
        "TOOLONGTICKER",  # > 10 chars
        "AAPL!",
        "../etc/passwd",
        "AA PL",
        "AA/PL",
    ],
)
def test_normalize_ticker_rejects_invalid_symbols(raw: str) -> None:
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


def test_write_atomic_writes_full_content(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "file.bin"
    write_atomic(path, b"hello world")
    assert path.read_bytes() == b"hello world"


def test_write_atomic_leaves_no_tmp_file_on_success(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    write_atomic(path, b"data")
    leftovers = [p for p in tmp_path.iterdir() if p.name != "file.bin"]
    assert leftovers == []


def test_store_company_dir_validates_ticker(store: LocalDocumentStore) -> None:
    with pytest.raises(InvalidTickerError):
        store.company_dir("../evil")


def test_doc_dir_layout(store: LocalDocumentStore) -> None:
    path = store.doc_dir("aapl", DocType.TEN_K, create=True)
    assert path == store.root / "AAPL" / "10-K"
    assert path.is_dir()


def test_earnings_calls_folder_name(store: LocalDocumentStore) -> None:
    path = store.doc_dir("AAPL", DocType.EARNINGS_CALL)
    assert path.name == "EarningsCalls"


def test_reports_dir_separate_from_doc_dirs(store: LocalDocumentStore) -> None:
    reports = store.reports_dir("AAPL", create=True)
    assert reports.name == "reports"
    assert reports.parent == store.company_dir("AAPL")


def test_save_document_rejects_path_traversal(store: LocalDocumentStore) -> None:
    with pytest.raises(ValueError, match="path separators"):
        store.save_document("AAPL", DocType.TEN_K, "../evil.pdf", b"x")
    with pytest.raises(ValueError, match="path separators"):
        store.save_document("AAPL", DocType.TEN_K, "sub/evil.pdf", b"x")


def test_save_and_list_documents(store: LocalDocumentStore) -> None:
    store.save_document("AAPL", DocType.TEN_K, "AAPL_FY_2024_01_01_10-K.pdf", b"a")
    store.save_document("AAPL", DocType.TEN_K, "AAPL_FY_2023_01_01_10-K.pdf", b"b")
    names = store.existing_filenames("AAPL", DocType.TEN_K)
    assert names == {"AAPL_FY_2024_01_01_10-K.pdf", "AAPL_FY_2023_01_01_10-K.pdf"}
    # Newest name first (lexicographic on the date-carrying filename).
    listed = store.list_documents("AAPL", DocType.TEN_K)
    assert next(p.name for p in listed) == "AAPL_FY_2024_01_01_10-K.pdf"


def test_save_document_is_idempotent_on_disk(store: LocalDocumentStore) -> None:
    name = "AAPL_FY_2024_01_01_10-K.pdf"
    store.save_document("AAPL", DocType.TEN_K, name, b"first")
    store.save_document("AAPL", DocType.TEN_K, name, b"second")
    docs = store.list_documents("AAPL", DocType.TEN_K)
    assert len(docs) == 1
    assert docs[0].read_bytes() == b"second"


def test_all_documents_grouped_by_type_and_empty_for_missing(store: LocalDocumentStore) -> None:
    store.save_document("AAPL", DocType.TEN_K, "AAPL_FY_2024_01_01_10-K.pdf", b"a")
    grouped = store.all_documents("AAPL")
    assert set(grouped) == set(DocType)
    assert len(grouped[DocType.TEN_K]) == 1
    assert grouped[DocType.TEN_Q] == []


def test_all_documents_never_includes_reports_folder(store: LocalDocumentStore) -> None:
    store.save_document("AAPL", DocType.TEN_K, "AAPL_FY_2024_01_01_10-K.pdf", b"a")
    reports = store.reports_dir("AAPL", create=True)
    (reports / "AAPL_report_20240101_000000.pdf").write_bytes(b"report")
    grouped = store.all_documents("AAPL")
    total = sum(len(v) for v in grouped.values())
    assert total == 1  # the report PDF must never be counted as a source document


def test_list_reports_excludes_dotfiles_and_sorts_by_mtime(store: LocalDocumentStore, tmp_path: Path) -> None:
    import os
    import time

    reports = store.reports_dir("AAPL", create=True)
    (reports / ".DS_Store").write_bytes(b"junk")
    old = reports / "AAPL_report_old.md"
    new = reports / "AAPL_report_new.md"
    old.write_text("old", encoding="utf-8")
    time.sleep(0.01)
    new.write_text("new", encoding="utf-8")
    os.utime(old, (time.time() - 100, time.time() - 100))
    listed = store.list_reports("AAPL")
    assert [p.name for p in listed] == ["AAPL_report_new.md", "AAPL_report_old.md"]


def test_list_companies_filters_valid_tickers_only(store: LocalDocumentStore) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "AAPL").mkdir()
    (store.root / "MSFT").mkdir()
    (store.root / ".cache").mkdir()
    (store.root / ".settings").mkdir()
    (store.root / "not a ticker!").mkdir()
    assert store.list_companies() == ["AAPL", "MSFT"]


def test_list_companies_on_missing_root(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "does_not_exist")
    assert store.list_companies() == []
