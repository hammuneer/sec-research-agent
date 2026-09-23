"""Tests for :mod:`sec_research_agent.pipeline.collect` (no network; sources are faked)."""

from __future__ import annotations

import logging
from datetime import date

import pytest

from sec_research_agent.config import DocumentLimits
from sec_research_agent.models import DocType
from sec_research_agent.pipeline.collect import DocumentCollector, limit_for
from sec_research_agent.sources.earnings import EarningsCall
from sec_research_agent.sources.errors import ProviderError
from sec_research_agent.storage import LocalDocumentStore


def test_limit_for_maps_every_doc_type() -> None:
    limits = DocumentLimits(annual_reports=5, quarterly_reports=8, proxy_statements=1, earnings_calls=8)
    assert limit_for(limits, DocType.TEN_K) == 5
    assert limit_for(limits, DocType.TEN_Q) == 8
    assert limit_for(limits, DocType.PROXY) == 1
    assert limit_for(limits, DocType.EARNINGS_CALL) == 8


class _FakeSecClient:
    def __init__(self, filings_by_type: dict[DocType, list[dict]]) -> None:
        self.filings_by_type = filings_by_type
        self.search_calls: list[tuple[str, DocType, int]] = []
        self.download_calls: list[dict] = []
        self.pdf_content = b"%PDF-fake"

    def search(self, ticker: str, doc_type: DocType, limit: int) -> list[dict]:
        self.search_calls.append((ticker, doc_type, limit))
        return self.filings_by_type.get(doc_type, [])[:limit]

    def download_pdf(self, filing: dict) -> bytes | None:
        self.download_calls.append(filing)
        return filing.get("_content", self.pdf_content)


class _FakeEarningsClient:
    def __init__(self, calls: list[EarningsCall], transcripts: dict[str, str | None] | None = None) -> None:
        self.calls = calls
        self.transcripts = transcripts or {}
        self.transcript_calls: list[str] = []

    def recent_calls(self, ticker: str, limit: int):
        return object(), self.calls[:limit]

    def transcript(self, company, call: EarningsCall) -> str | None:
        self.transcript_calls.append(call.period_key)
        return self.transcripts.get(call.period_key, "Default transcript text.")


@pytest.fixture
def limits() -> DocumentLimits:
    return DocumentLimits(annual_reports=2, quarterly_reports=2, proxy_statements=1, earnings_calls=2)


def test_collect_downloads_missing_sec_filings(store: LocalDocumentStore, limits: DocumentLimits) -> None:
    filings = {
        DocType.TEN_K: [{"periodOfReport": "2024-01-01"}, {"periodOfReport": "2023-01-01"}],
    }
    sec = _FakeSecClient(filings)
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")

    assert result.downloaded[DocType.TEN_K] == 2
    assert result.on_disk[DocType.TEN_K] == 2
    assert len(store.list_documents("AAPL", DocType.TEN_K)) == 2


def test_collect_is_idempotent_skips_already_downloaded(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    filings = {DocType.TEN_K: [{"periodOfReport": "2024-01-01"}]}
    sec = _FakeSecClient(filings)
    collector = DocumentCollector(store, limits, sec, None)
    collector.collect("AAPL")
    assert sec.search_calls.count(("AAPL", DocType.TEN_K, 2)) == 1

    # Second run: the filing is already on disk, so it must not be re-downloaded.
    result2 = collector.collect("AAPL")
    assert result2.downloaded[DocType.TEN_K] == 0
    assert result2.on_disk[DocType.TEN_K] == 1
    assert len(sec.download_calls) == 1  # still just the one download from the first run


def test_collect_skips_type_when_limit_already_met_without_searching(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    sec = _FakeSecClient({DocType.PROXY: [{"periodOfReport": "2024-01-01"}]})
    collector = DocumentCollector(store, limits, sec, None)
    collector.collect("AAPL")  # downloads the one proxy allowed (limit=1)
    sec.search_calls.clear()

    collector.collect("AAPL")
    assert not any(t is DocType.PROXY for _, t, _ in sec.search_calls)


def test_collect_zero_limit_skips_type_entirely(store: LocalDocumentStore) -> None:
    limits = DocumentLimits(annual_reports=0, quarterly_reports=0, proxy_statements=0, earnings_calls=0)
    sec = _FakeSecClient({DocType.TEN_K: [{"periodOfReport": "2024-01-01"}]})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")
    assert result.total_downloaded == 0
    assert sec.search_calls == []


def test_collect_records_error_when_sec_client_missing(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    collector = DocumentCollector(store, limits, None, None)
    result = collector.collect("AAPL")
    assert result.total_downloaded == 0
    assert any("SEC_API_KEY" in e for e in result.errors)


def test_collect_continues_other_types_after_one_type_fails(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    class _BrokenSecClient(_FakeSecClient):
        def search(self, ticker, doc_type, limit):
            if doc_type is DocType.TEN_K:
                raise RuntimeError("SEC API down")
            return super().search(ticker, doc_type, limit)

    sec = _BrokenSecClient({DocType.TEN_Q: [{"periodOfReport": "2024-03-31"}]})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")

    assert any("10-K" in e for e in result.errors)
    assert result.downloaded[DocType.TEN_Q] == 1  # other doc types still collected


def test_collect_skips_download_when_pdf_endpoint_returns_none(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    sec = _FakeSecClient({DocType.TEN_K: [{"periodOfReport": "2024-01-01", "_content": None}]})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")
    assert result.downloaded[DocType.TEN_K] == 0
    assert store.list_documents("AAPL", DocType.TEN_K) == []


def test_collect_downloads_earnings_calls(store: LocalDocumentStore, limits: DocumentLimits) -> None:
    calls = [
        EarningsCall(2024, 1, date(2024, 2, 1), event=None),
        EarningsCall(2024, 2, date(2024, 5, 1), event=None),
    ]
    earnings = _FakeEarningsClient(calls)
    collector = DocumentCollector(store, limits, None, earnings)
    result = collector.collect("AAPL")
    assert result.downloaded[DocType.EARNINGS_CALL] == 2
    assert len(store.list_documents("AAPL", DocType.EARNINGS_CALL)) == 2


def test_collect_earnings_idempotent_by_fiscal_period(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    calls = [EarningsCall(2024, 1, date(2024, 2, 1), event=None)]
    earnings = _FakeEarningsClient(calls)
    collector = DocumentCollector(store, limits, None, earnings)
    collector.collect("AAPL")
    assert len(earnings.transcript_calls) == 1

    collector.collect("AAPL")
    assert len(earnings.transcript_calls) == 1  # not re-fetched


def test_collect_earnings_skips_calls_with_no_transcript(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    calls = [EarningsCall(2024, 1, date(2024, 2, 1), event=None)]
    earnings = _FakeEarningsClient(calls, transcripts={"FY2024_Q1": None})
    collector = DocumentCollector(store, limits, None, earnings)
    result = collector.collect("AAPL")
    assert result.downloaded[DocType.EARNINGS_CALL] == 0


def test_collect_records_error_when_earnings_client_missing(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    collector = DocumentCollector(store, limits, None, None)
    result = collector.collect("AAPL")
    assert any("EARNINGSCALL_API_KEY" in e for e in result.errors)


def test_collection_result_totals(store: LocalDocumentStore, limits: DocumentLimits) -> None:
    sec = _FakeSecClient({DocType.TEN_K: [{"periodOfReport": "2024-01-01"}]})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")
    assert result.total_downloaded == result.total_on_disk == 1
    assert result.elapsed_seconds >= 0


def test_on_status_callback_invoked(store: LocalDocumentStore, limits: DocumentLimits) -> None:
    sec = _FakeSecClient({DocType.TEN_K: [{"periodOfReport": "2024-01-01"}]})
    collector = DocumentCollector(store, limits, sec, None)
    events: list[tuple[str, str]] = []
    collector.collect("AAPL", on_status=lambda step, msg: events.append((step, msg)))
    assert any(step == "10-K" for step, _ in events)


# --------------------------------------------------------------------------- provider errors


class _FatalSecClient(_FakeSecClient):
    """Raises a fatal :class:`ProviderError` (e.g. quota exhausted) on every search."""

    def search(self, ticker: str, doc_type: DocType, limit: int) -> list[dict]:
        self.search_calls.append((ticker, doc_type, limit))
        raise ProviderError("sec-api quota exceeded - upgrade plan or wait", fatal=True)


def test_fatal_provider_error_stops_hammering_remaining_doc_types_for_the_ticker(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    sec = _FatalSecClient({})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")

    # 10-K is searched (and fails); 10-Q and Proxy must be skipped without calling search again.
    assert len(sec.search_calls) == 1
    assert result.errors[0].startswith("10-K: sec-api quota exceeded")
    assert any("skipped" in e for e in result.errors[1:])


def test_fatal_provider_error_persists_across_tickers_in_the_same_run(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    sec = _FatalSecClient({})
    collector = DocumentCollector(store, limits, sec, None)
    collector.collect("AAPL")
    assert len(sec.search_calls) == 1

    # A second ticker in the same run must not hit the disabled provider at all.
    collector.collect("MSFT")
    assert len(sec.search_calls) == 1


def test_non_fatal_provider_error_does_not_disable_the_provider(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    class _NotFoundOnceSecClient(_FakeSecClient):
        def search(self, ticker: str, doc_type: DocType, limit: int) -> list[dict]:
            self.search_calls.append((ticker, doc_type, limit))
            if doc_type is DocType.TEN_K:
                raise ProviderError("sec-api: not found (HTTP 404)", fatal=False)
            return super().search(ticker, doc_type, limit)

    sec = _NotFoundOnceSecClient({DocType.TEN_Q: [{"periodOfReport": "2024-03-31"}]})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")

    assert {t for _, t, _ in sec.search_calls} == {DocType.TEN_K, DocType.TEN_Q, DocType.PROXY}
    assert result.downloaded[DocType.TEN_Q] == 1


def test_provider_error_logs_one_clean_line_without_traceback(
    store: LocalDocumentStore, limits: DocumentLimits, caplog: pytest.LogCaptureFixture
) -> None:
    sec = _FatalSecClient({})
    earnings = _FakeEarningsClient([])  # avoid an unrelated "key not configured" failure
    collector = DocumentCollector(store, limits, sec, earnings)
    with caplog.at_level(logging.DEBUG, logger="sec_research_agent.pipeline.collect"):
        collector.collect("AAPL")

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records
    assert all(r.exc_info is None for r in error_records)
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(r.exc_info is not None for r in debug_records)


def test_fatal_provider_error_does_not_compound_the_skip_message(
    store: LocalDocumentStore, limits: DocumentLimits
) -> None:
    """Regression test: the second and third skipped doc types must not accumulate a
    "skipped - skipped - ..." prefix; they all report the original reason."""
    sec = _FatalSecClient({})
    collector = DocumentCollector(store, limits, sec, None)
    result = collector.collect("AAPL")
    assert not any("skipped - skipped" in e for e in result.errors)


def test_unexpected_error_still_logs_full_traceback(
    store: LocalDocumentStore, limits: DocumentLimits, caplog: pytest.LogCaptureFixture
) -> None:
    class _BrokenSecClient(_FakeSecClient):
        def search(self, ticker, doc_type, limit):
            raise RuntimeError("something genuinely unexpected")

    sec = _BrokenSecClient({})
    collector = DocumentCollector(store, limits, sec, None)
    with caplog.at_level(logging.ERROR, logger="sec_research_agent.pipeline.collect"):
        collector.collect("AAPL")

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records
    assert any(r.exc_info is not None for r in error_records)
