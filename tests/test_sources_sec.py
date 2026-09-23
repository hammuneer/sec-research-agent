"""Tests for :mod:`sec_research_agent.sources.sec` (no network; sec-api is monkeypatched)."""

from __future__ import annotations

import pytest

from sec_research_agent.models import DocType
from sec_research_agent.sources import sec as sec_module
from sec_research_agent.sources.errors import ProviderError
from sec_research_agent.sources.sec import SecFilingsClient, build_sec_filename, dedupe_by_period


def test_build_sec_filename_annual_report() -> None:
    filing = {"periodOfReport": "2025-01-26"}
    assert build_sec_filename("NVDA", filing, DocType.TEN_K) == "NVDA_FY_2025_01_26_10-K.pdf"


def test_build_sec_filename_proxy_uses_fy_label() -> None:
    filing = {"periodOfReport": "2025-03-01"}
    assert build_sec_filename("NVDA", filing, DocType.PROXY) == "NVDA_FY_2025_03_01_Proxy.pdf"


@pytest.mark.parametrize(
    ("month", "expected_quarter"),
    [("01", "Q1"), ("03", "Q1"), ("04", "Q2"), ("06", "Q2"), ("07", "Q3"), ("10", "Q4"), ("12", "Q4")],
)
def test_build_sec_filename_quarterly_report_quarter_mapping(month: str, expected_quarter: str) -> None:
    filing = {"periodOfReport": f"2025-{month}-15"}
    name = build_sec_filename("NVDA", filing, DocType.TEN_Q)
    assert name == f"NVDA_{expected_quarter}_2025_{month}_15_10-Q.pdf"


def test_build_sec_filename_falls_back_to_filed_at_when_period_missing() -> None:
    filing = {"filedAt": "2025-02-10T00:00:00-05:00"}
    assert build_sec_filename("NVDA", filing, DocType.TEN_K) == "NVDA_FY_2025_02_10_10-K.pdf"


def test_build_sec_filename_handles_missing_date_entirely() -> None:
    name = build_sec_filename("NVDA", {}, DocType.TEN_K)
    assert name == "NVDA_NA_10-K.pdf"


def test_dedupe_by_period_keeps_first_per_period_and_respects_limit() -> None:
    filings = [
        {"periodOfReport": "2025-01-26", "filedAt": "2025-02-01"},  # original
        {"periodOfReport": "2025-01-26", "filedAt": "2025-03-01"},  # amendment, dropped
        {"periodOfReport": "2024-01-26", "filedAt": "2024-02-01"},
        {"periodOfReport": "2023-01-26", "filedAt": "2023-02-01"},
    ]
    result = dedupe_by_period(filings, limit=2)
    assert len(result) == 2
    assert result[0]["filedAt"] == "2025-02-01"
    assert result[1]["periodOfReport"] == "2024-01-26"


def test_dedupe_by_period_drops_filings_without_a_period() -> None:
    filings = [{"filedAt": "2025-02-01"}, {"periodOfReport": "2024-01-26"}]
    result = dedupe_by_period(filings, limit=10)
    assert len(result) == 1


def test_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="SEC_API_KEY"):
        SecFilingsClient("")


class _FakeQueryApi:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.last_query: dict | None = None

    def get_filings(self, query: dict) -> dict:
        self.last_query = query
        return {
            "filings": [
                {"periodOfReport": "2025-01-26", "linkToFilingDetails": "https://example.com/a"},
                {"periodOfReport": "2024-01-26", "linkToFilingDetails": "https://example.com/b"},
            ]
        }


class _FakePdfGeneratorApi:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.requested_urls: list[str] = []

    def get_pdf(self, url: str) -> bytes:
        self.requested_urls.append(url)
        if url.endswith("/broken"):
            return b"not a pdf"
        return b"%PDF-1.4 fake pdf content"


@pytest.fixture
def patched_sec_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sec_module, "QueryApi", _FakeQueryApi)
    monkeypatch.setattr(sec_module, "PdfGeneratorApi", _FakePdfGeneratorApi)
    return sec_module


def test_search_builds_query_and_dedupes(patched_sec_api) -> None:
    client = SecFilingsClient("test-key")
    results = client.search("NVDA", DocType.TEN_K, limit=5)
    assert len(results) == 2
    query = client._query.last_query
    assert query is not None
    assert "ticker:NVDA" in query["query"]
    assert 'formType:"10-K"' in query["query"]


def test_download_pdf_returns_none_without_url(patched_sec_api) -> None:
    client = SecFilingsClient("test-key")
    assert client.download_pdf({}) is None


def test_download_pdf_returns_none_for_non_pdf_response(patched_sec_api) -> None:
    client = SecFilingsClient("test-key")
    result = client.download_pdf({"linkToFilingDetails": "https://example.com/broken"})
    assert result is None


def test_download_pdf_returns_content_on_success(patched_sec_api) -> None:
    client = SecFilingsClient("test-key")
    result = client.download_pdf({"linkToFilingDetails": "https://example.com/good"})
    assert result == b"%PDF-1.4 fake pdf content"


# --------------------------------------------------------------------------- provider errors


class _FailingQueryApi:
    def __init__(self, api_key: str, error_message: str) -> None:
        self.api_key = api_key
        self._error_message = error_message

    def get_filings(self, query: dict) -> dict:
        raise Exception(self._error_message)  # mirrors sec_api's own plain Exception


def _client_with_search_error(monkeypatch: pytest.MonkeyPatch, message: str) -> SecFilingsClient:
    monkeypatch.setattr(sec_module, "QueryApi", lambda api_key: _FailingQueryApi(api_key, message))
    return SecFilingsClient("test-key")


def test_search_raises_fatal_provider_error_on_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_search_error(monkeypatch, "API error: 401 - Unauthorized")
    with pytest.raises(ProviderError) as exc_info:
        client.search("NVDA", DocType.TEN_K, limit=5)
    assert exc_info.value.fatal is True
    assert "SEC_API_KEY" in str(exc_info.value)


def test_search_raises_fatal_provider_error_on_quota_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_search_error(monkeypatch, "API error: 429 - Too Many Requests")
    with pytest.raises(ProviderError) as exc_info:
        client.search("NVDA", DocType.TEN_K, limit=5)
    assert exc_info.value.fatal is True
    assert "quota" in str(exc_info.value).lower()


def test_search_raises_non_fatal_provider_error_on_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_search_error(monkeypatch, "API error: 404 - Not Found")
    with pytest.raises(ProviderError) as exc_info:
        client.search("NVDA", DocType.TEN_K, limit=5)
    assert exc_info.value.fatal is False


def test_search_reraises_unrecognized_errors_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_search_error(monkeypatch, "boom, totally unexpected")
    with pytest.raises(Exception, match="boom, totally unexpected") as exc_info:
        client.search("NVDA", DocType.TEN_K, limit=5)
    assert not isinstance(exc_info.value, ProviderError)
