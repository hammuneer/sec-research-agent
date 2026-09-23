"""Tests for :mod:`sec_research_agent.sources.earnings` (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from earningscall.errors import InsufficientApiAccessError, InvalidApiKeyError

from sec_research_agent.sources import earnings as earnings_module
from sec_research_agent.sources.earnings import (
    EarningsCall,
    EarningsCallClient,
    build_earnings_filename,
    existing_periods,
    render_transcript_pdf,
)
from sec_research_agent.sources.errors import ProviderError


def test_build_earnings_filename() -> None:
    call = EarningsCall(fiscal_year=2026, quarter=3, call_date=date(2025, 11, 19), event=None)
    assert build_earnings_filename("NVDA", call) == "NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf"


def test_period_key() -> None:
    call = EarningsCall(fiscal_year=2026, quarter=3, call_date=date(2025, 11, 19), event=None)
    assert call.period_key == "FY2026_Q3"


def test_existing_periods_new_format() -> None:
    names = ["NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf"]
    assert existing_periods(names) == {"FY2026_Q3"}


def test_existing_periods_legacy_dated_format() -> None:
    # Legacy: `_Q<quarter>_YYYY_MM_DD_EarningsCall.pdf`, year taken from the calendar date.
    names = ["NVDA_Q2_2024_08_15_EarningsCall.pdf"]
    assert existing_periods(names) == {"FY2024_Q2"}


def test_existing_periods_legacy_short_format() -> None:
    names = ["NVDA_2023_Q1_EarningsCall.pdf"]
    assert existing_periods(names) == {"FY2023_Q1"}


def test_existing_periods_mixed_and_unrecognized() -> None:
    names = [
        "NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf",
        "NVDA_2023_Q1_EarningsCall.pdf",
        "not_a_transcript.pdf",
    ]
    assert existing_periods(names) == {"FY2026_Q3", "FY2023_Q1"}


def test_render_transcript_pdf_produces_valid_pdf() -> None:
    data = render_transcript_pdf("Operator: Welcome to the call.\n\nCEO: Thank you.", "AAPL Q1 2025 call")
    assert data.startswith(b"%PDF")
    assert len(data) > 100


def test_earnings_call_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="EARNINGSCALL_API_KEY"):
        EarningsCallClient("")


class _FakeEvent:
    def __init__(self, year: int, quarter: int, conference_date) -> None:
        self.year = year
        self.quarter = quarter
        self.conference_date = conference_date


class _FakeCompany:
    def __init__(
        self, events: list[_FakeEvent], transcript_text: str | None = "Hello, this is a call."
    ) -> None:
        self._events = events
        self._transcript_text = transcript_text

    def events(self) -> list[_FakeEvent]:
        return self._events

    def get_transcript(self, event: _FakeEvent):
        from types import SimpleNamespace

        return SimpleNamespace(text=self._transcript_text)


@pytest.fixture
def client() -> EarningsCallClient:
    return EarningsCallClient("test-key")


def test_recent_calls_skips_events_with_no_conference_date(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    company = _FakeCompany(
        [
            _FakeEvent(2025, 1, None),  # no conference date: must be skipped, not crash
            _FakeEvent(2025, 2, date(2025, 5, 1)),
        ]
    )
    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: company)
    _, calls = client.recent_calls("NVDA", limit=10)
    assert len(calls) == 1
    assert calls[0].quarter == 2


def test_recent_calls_excludes_future_events(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    today = datetime.now(UTC).date()
    company = _FakeCompany(
        [
            _FakeEvent(2025, 1, today + timedelta(days=30)),  # future call, must be excluded
            _FakeEvent(2025, 2, today - timedelta(days=10)),
        ]
    )
    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: company)
    _, calls = client.recent_calls("NVDA", limit=10)
    assert len(calls) == 1
    assert calls[0].quarter == 2


def test_recent_calls_sorted_newest_first_and_respects_limit(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    company = _FakeCompany(
        [
            _FakeEvent(2024, 1, date(2024, 2, 1)),
            _FakeEvent(2024, 2, date(2024, 5, 1)),
            _FakeEvent(2024, 3, date(2024, 8, 1)),
        ]
    )
    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: company)
    _, calls = client.recent_calls("NVDA", limit=2)
    assert [c.quarter for c in calls] == [3, 2]


def test_recent_calls_handles_unknown_ticker(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: None)
    company, calls = client.recent_calls("ZZZZ", limit=10)
    assert company is None
    assert calls == []


def test_recent_calls_normalizes_datetime_with_timezone(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    aware_dt = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    company = _FakeCompany([_FakeEvent(2024, 1, aware_dt)])
    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: company)
    _, calls = client.recent_calls("NVDA", limit=10)
    assert calls[0].call_date == date(2024, 3, 1)


def test_transcript_returns_none_for_empty_text(client: EarningsCallClient) -> None:
    company = _FakeCompany([], transcript_text="   ")
    call = EarningsCall(2024, 1, date(2024, 1, 1), event=None)
    assert client.transcript(company, call) is None


def test_transcript_returns_text_when_present(client: EarningsCallClient) -> None:
    company = _FakeCompany([], transcript_text="Actual transcript content.")
    call = EarningsCall(2024, 1, date(2024, 1, 1), event=None)
    assert client.transcript(company, call) == "Actual transcript content."


# --------------------------------------------------------------------------- provider errors


def test_recent_calls_raises_fatal_provider_error_on_invalid_key(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    def _boom(ticker: str):
        raise InvalidApiKeyError("bad key")

    monkeypatch.setattr(earnings_module, "get_company", _boom)
    with pytest.raises(ProviderError) as exc_info:
        client.recent_calls("NVDA", limit=10)
    assert exc_info.value.fatal is True
    assert "EARNINGSCALL_API_KEY" in str(exc_info.value)


def test_recent_calls_raises_fatal_provider_error_on_insufficient_access(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    class _RestrictedCompany(_FakeCompany):
        def events(self) -> list:
            raise InsufficientApiAccessError("plan too low")

    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: _RestrictedCompany([]))
    with pytest.raises(ProviderError) as exc_info:
        client.recent_calls("NVDA", limit=10)
    assert exc_info.value.fatal is True


def test_recent_calls_raises_fatal_provider_error_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    response = SimpleNamespace(status_code=429)

    class _RateLimitedCompany(_FakeCompany):
        def events(self) -> list:
            raise requests.exceptions.HTTPError(response=response)

    monkeypatch.setattr(earnings_module, "get_company", lambda ticker: _RateLimitedCompany([]))
    with pytest.raises(ProviderError) as exc_info:
        client.recent_calls("NVDA", limit=10)
    assert exc_info.value.fatal is True


def test_recent_calls_reraises_unrecognized_errors_unchanged(
    monkeypatch: pytest.MonkeyPatch, client: EarningsCallClient
) -> None:
    def _boom(ticker: str):
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr(earnings_module, "get_company", _boom)
    with pytest.raises(RuntimeError, match="totally unexpected") as exc_info:
        client.recent_calls("NVDA", limit=10)
    assert not isinstance(exc_info.value, ProviderError)


def test_transcript_raises_non_fatal_provider_error_on_not_found(
    client: EarningsCallClient,
) -> None:
    response = SimpleNamespace(status_code=404)

    class _NotFoundCompany:
        def get_transcript(self, event):
            raise requests.exceptions.HTTPError(response=response)

    call = EarningsCall(2024, 1, date(2024, 1, 1), event=None)
    with pytest.raises(ProviderError) as exc_info:
        client.transcript(_NotFoundCompany(), call)
    assert exc_info.value.fatal is False
