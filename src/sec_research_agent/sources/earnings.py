"""Earnings call transcripts via https://earningscall.biz.

File naming: ``TICKER_FY<fiscal year>_Q<fiscal quarter>_YYYY_MM_DD_EarningsCall.pdf`` where the
date is the calendar date of the call, e.g. ``NVDA_FY2026_Q3_2025_11_19_EarningsCall.pdf``.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from xml.sax.saxutils import escape

import earningscall
import requests
from earningscall import get_company
from earningscall.errors import InsufficientApiAccessError, InvalidApiKeyError
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from ..retry import retry
from .errors import ProviderError

logger = logging.getLogger(__name__)

_NEW_FORMAT = re.compile(r"_FY(\d{4})_Q([1-4])_\d{4}_\d{2}_\d{2}_EarningsCall\.pdf$", re.IGNORECASE)
_LEGACY_DATED = re.compile(r"_Q([1-4])_(\d{4})_\d{2}_\d{2}_EarningsCall\.pdf$", re.IGNORECASE)
_LEGACY_SHORT = re.compile(r"_(\d{4})_Q([1-4])_EarningsCall\.pdf$", re.IGNORECASE)


def _classify(exc: Exception) -> ProviderError | None:
    """Map an earningscall failure to a :class:`ProviderError`, or ``None`` if not recognized."""
    if isinstance(exc, InvalidApiKeyError):
        return ProviderError(
            f"earningscall rejected the API key - check EARNINGSCALL_API_KEY. ({exc})", fatal=True
        )
    if isinstance(exc, InsufficientApiAccessError):
        return ProviderError(f"earningscall: plan does not include this data - {exc}", fatal=True)
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 429:
            return ProviderError("earningscall rate-limited (HTTP 429) - wait before retrying.", fatal=True)
        if status == 404:
            return ProviderError("earningscall: not found (HTTP 404)")
        return ProviderError(f"earningscall request failed (HTTP {status})")
    return None


@dataclass(frozen=True)
class EarningsCall:
    """One past earnings call."""

    fiscal_year: int
    quarter: int
    call_date: date
    event: Any  # earningscall.event.EarningsEvent, needed to fetch the transcript

    @property
    def period_key(self) -> str:
        """Fiscal period identifier, e.g. ``FY2026_Q3``."""
        return f"FY{self.fiscal_year}_Q{self.quarter}"


def build_earnings_filename(ticker: str, call: EarningsCall) -> str:
    """Deterministic PDF filename for ``call``."""
    d = call.call_date
    return f"{ticker}_{call.period_key}_{d.year:04d}_{d.month:02d}_{d.day:02d}_EarningsCall.pdf"


def existing_periods(filenames: Iterable[str]) -> set[str]:
    """Fiscal periods (``FY2026_Q3``) already on disk, including legacy file names."""
    periods: set[str] = set()
    for name in filenames:
        for pattern, year_group, quarter_group in (
            (_NEW_FORMAT, 1, 2),
            (_LEGACY_DATED, 2, 1),
            (_LEGACY_SHORT, 1, 2),
        ):
            match = pattern.search(name)
            if match:
                periods.add(f"FY{match.group(year_group)}_Q{match.group(quarter_group)}")
                break
    return periods


def _to_date(value: Any) -> date | None:
    """Normalise the conference date returned by the API to a UTC calendar date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return None


def render_transcript_pdf(text: str, title: str) -> bytes:
    """Render plain transcript text into a simple, readable PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, title=title)
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Transcript",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        alignment=TA_JUSTIFY,
        leftIndent=0.5 * inch,
        rightIndent=0.5 * inch,
    )
    story: list[Any] = [Paragraph(escape(title), styles["Heading2"]), Spacer(1, 0.15 * inch)]
    for line in text.splitlines():
        line = line.strip()
        if line:
            story.append(Paragraph(escape(line), body))
            story.append(Spacer(1, 0.05 * inch))
        else:
            story.append(Spacer(1, 0.1 * inch))
    doc.build(story)
    return buffer.getvalue()


class EarningsCallClient:
    """Fetch past earnings calls and their transcripts."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("EARNINGSCALL_API_KEY is not configured")
        # The library is configured through module state; set it when the client is built.
        earningscall.api_key = api_key

    def recent_calls(self, ticker: str, limit: int) -> tuple[Any, list[EarningsCall]]:
        """Return the company handle and up to ``limit`` most recent calls that already happened."""
        try:
            company = retry(lambda: get_company(ticker.lower()), description=f"earningscall company {ticker}")
        except Exception as exc:
            provider_error = _classify(exc)
            if provider_error is None:
                raise
            raise provider_error from exc
        if company is None:
            logger.warning("[%s] Not covered by the earnings call API", ticker)
            return None, []
        try:
            events = retry(company.events, description=f"earningscall events {ticker}")
        except Exception as exc:
            provider_error = _classify(exc)
            if provider_error is None:
                raise
            raise provider_error from exc
        today = datetime.now(UTC).date()

        calls: list[EarningsCall] = []
        for event in events:
            call_date = _to_date(getattr(event, "conference_date", None))
            if call_date is None:
                logger.warning(
                    "[%s] Skipping FY%s Q%s: no conference date", ticker, event.year, event.quarter
                )
                continue
            if call_date > today:
                continue
            calls.append(EarningsCall(int(event.year), int(event.quarter), call_date, event))
        calls.sort(key=lambda c: c.call_date, reverse=True)
        return company, calls[:limit]

    def transcript(self, company: Any, call: EarningsCall) -> str | None:
        """Transcript text for ``call`` or ``None`` if unavailable."""
        try:
            result = retry(
                lambda: company.get_transcript(event=call.event),
                description=f"earningscall transcript {call.period_key}",
            )
        except Exception as exc:
            provider_error = _classify(exc)
            if provider_error is None:
                raise
            raise provider_error from exc
        text = getattr(result, "text", None)
        return text if text and text.strip() else None
