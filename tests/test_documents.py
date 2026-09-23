"""Tests for :mod:`sec_research_agent.documents`."""

from __future__ import annotations

from pathlib import Path

from conftest import write_pdf
from sec_research_agent.documents import (
    build_document,
    discover_documents,
    extract_all,
    extract_pdf_text,
    parse_period_date,
)
from sec_research_agent.models import DocType
from sec_research_agent.storage import LocalDocumentStore


def test_parse_period_date_new_format() -> None:
    assert parse_period_date("NVDA_FY_2025_01_26_10-K.pdf") == "2025-01-26"


def test_parse_period_date_earnings_format() -> None:
    assert parse_period_date("NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf") == "2025-11-19"


def test_parse_period_date_legacy_year_quarter() -> None:
    # Legacy files had no explicit calendar date, only "<year>_Q<quarter>".
    assert parse_period_date("NVDA_2024_Q3_EarningsCall.pdf") == "2024-09-30"


def test_parse_period_date_unknown_returns_empty_string() -> None:
    assert parse_period_date("no_date_here.pdf") == ""


def test_build_document_extracts_fiscal_year_and_quarter() -> None:
    doc = build_document(Path("NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf"), DocType.EARNINGS_CALL)
    assert doc.fiscal_year == "2026"
    assert doc.quarter == "Q3"
    assert doc.period_date == "2025-11-19"


def test_build_document_fiscal_year_falls_back_to_period_date_year() -> None:
    doc = build_document(Path("NVDA_FY_2025_01_26_10-K.pdf"), DocType.TEN_K)
    assert doc.fiscal_year == "2025"
    assert doc.quarter is None


def test_build_document_no_date_no_fiscal_year() -> None:
    doc = build_document(Path("mystery.pdf"), DocType.PROXY)
    assert doc.period_date == ""
    assert doc.fiscal_year is None


def test_discover_documents_ignores_reports_folder(store: LocalDocumentStore) -> None:
    write_pdf(store.doc_dir("AAPL", DocType.TEN_K, create=True) / "AAPL_FY_2024_01_01_10-K.pdf", "10-K text")
    write_pdf(
        store.doc_dir("AAPL", DocType.EARNINGS_CALL, create=True)
        / "AAPL_FY2024_Q1_2024_02_01_EarningsCall.pdf",
        "call text",
    )
    reports_dir = store.reports_dir("AAPL", create=True)
    write_pdf(reports_dir / "AAPL_report_20240101_000000.pdf", "generated report, not a source")

    documents = discover_documents(store, "AAPL")
    assert len(documents) == 2
    assert {d.doc_type for d in documents} == {DocType.TEN_K, DocType.EARNINGS_CALL}
    assert all(d.path.parent.name != "reports" for d in documents)


def test_discover_documents_classifies_by_folder_not_path_substring(store: LocalDocumentStore) -> None:
    # A filename that happens to contain another doc type's folder name as a substring must
    # still be classified by the folder it physically lives in.
    tricky_name = "AAPL_10-Q_pretending_10_01_2024.pdf"
    write_pdf(store.doc_dir("AAPL", DocType.TEN_K, create=True) / tricky_name, "annual text")
    documents = discover_documents(store, "AAPL")
    assert len(documents) == 1
    assert documents[0].doc_type is DocType.TEN_K


def test_discover_documents_sorted_by_type_then_filename(store: LocalDocumentStore) -> None:
    write_pdf(store.doc_dir("AAPL", DocType.TEN_Q, create=True) / "AAPL_Q2_2024_06_30_10-Q.pdf", "q2")
    write_pdf(store.doc_dir("AAPL", DocType.TEN_K, create=True) / "AAPL_FY_2024_01_01_10-K.pdf", "fy")
    documents = discover_documents(store, "AAPL")
    assert [d.doc_type for d in documents] == [DocType.TEN_K, DocType.TEN_Q]


def test_extract_pdf_text_reads_real_pdf(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "doc.pdf", "Revenue grew twenty percent year over year.")
    text, page_count = extract_pdf_text(path)
    assert "Revenue grew twenty percent" in text
    assert page_count >= 1


def test_extract_all_fills_text_and_drops_unreadable(store: LocalDocumentStore) -> None:
    good = store.doc_dir("AAPL", DocType.TEN_K, create=True) / "AAPL_FY_2024_01_01_10-K.pdf"
    write_pdf(good, "Some real annual report content.")
    bad = store.doc_dir("AAPL", DocType.TEN_Q, create=True) / "AAPL_Q1_2024_01_01_10-Q.pdf"
    bad.write_bytes(b"not a pdf at all")

    documents = discover_documents(store, "AAPL")
    assert len(documents) == 2
    readable = extract_all(documents, workers=2)

    assert len(readable) == 1
    assert readable[0].filename == good.name
    assert "annual report content" in readable[0].text
