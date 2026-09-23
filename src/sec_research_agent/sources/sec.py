"""SEC filings (10-K, 10-Q, DEF 14A) via https://sec-api.io.

File naming: ``TICKER_<PERIOD>_YYYY_MM_DD_<LABEL>.pdf`` where the date is the report period:

* 10-K / Proxy: ``NVDA_FY_2025_01_26_10-K.pdf``
* 10-Q:         ``NVDA_Q3_2025_10_26_10-Q.pdf`` (calendar quarter of the period end)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sec_api import PdfGeneratorApi, QueryApi

from ..models import DocType
from ..retry import retry
from .errors import ProviderError

logger = logging.getLogger(__name__)

SEC_FORM_TYPES: dict[DocType, str] = {
    DocType.TEN_K: "10-K",
    DocType.TEN_Q: "10-Q",
    DocType.PROXY: "DEF 14A",
}

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# sec-api's Python client raises a plain ``Exception("API error: <status> - <body>")`` for
# every non-200/429-retried response; this is the only way to recover the status code.
_API_ERROR = re.compile(r"API error:\s*(\d+)\s*-\s*(.*)", re.IGNORECASE | re.DOTALL)

Filing = dict[str, Any]


def _classify(exc: Exception) -> ProviderError | None:
    """Map a sec-api failure to a :class:`ProviderError`, or ``None`` if not recognized."""
    match = _API_ERROR.search(str(exc))
    if not match:
        return None
    status, body = int(match.group(1)), match.group(2).strip()[:200]
    if status in (401, 403):
        return ProviderError(f"sec-api rejected the API key (HTTP {status}) - check SEC_API_KEY.", fatal=True)
    if status == 429:
        return ProviderError(
            "sec-api quota exceeded or rate-limited (HTTP 429) - upgrade your plan or wait before retrying.",
            fatal=True,
        )
    if status == 404:
        return ProviderError(f"sec-api: not found (HTTP 404): {body}")
    return ProviderError(f"sec-api request failed (HTTP {status}): {body}")


def build_sec_filename(ticker: str, filing: Filing, doc_type: DocType) -> str:
    """Deterministic PDF filename for ``filing``."""
    date_str = (filing.get("periodOfReport") or filing.get("filedAt") or "").strip()
    match = _ISO_DATE.match(date_str)
    if not match:
        fallback = re.sub(r"[^0-9A-Za-z]+", "_", date_str) or "NA"
        return f"{ticker}_{fallback}_{doc_type.value}.pdf"
    yyyy, mm, dd = match.groups()
    period = f"Q{(int(mm) - 1) // 3 + 1}" if doc_type is DocType.TEN_Q else "FY"
    return f"{ticker}_{period}_{yyyy}_{mm}_{dd}_{doc_type.value}.pdf"


def dedupe_by_period(filings: list[Filing], limit: int) -> list[Filing]:
    """Keep the most recently filed filing per report period (drops amendments), up to ``limit``."""
    seen: set[str] = set()
    unique: list[Filing] = []
    for filing in filings:
        period = (filing.get("periodOfReport") or "").strip()
        if period and period not in seen:
            seen.add(period)
            unique.append(filing)
    return unique[:limit]


class SecFilingsClient:
    """Thin wrapper around sec-api's query and PDF generator endpoints."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("SEC_API_KEY is not configured")
        self._query = QueryApi(api_key)
        self._pdf = PdfGeneratorApi(api_key)

    def search(self, ticker: str, doc_type: DocType, limit: int) -> list[Filing]:
        """Most recent filings of ``doc_type`` for ``ticker`` (one per report period).

        ``ticker`` must already be validated (see :func:`storage.normalize_ticker`).
        """
        form = SEC_FORM_TYPES[doc_type]
        query = {
            "query": f'ticker:{ticker} AND formType:"{form}"',
            "from": "0",
            # Over-fetch so de-duplicating amendments still leaves `limit` periods.
            "size": str(min(limit * 3, 50)),
            "sort": [{"filedAt": {"order": "desc"}}],
        }
        try:
            result = retry(lambda: self._query.get_filings(query), description=f"SEC search {ticker} {form}")
        except Exception as exc:
            provider_error = _classify(exc)
            if provider_error is None:
                raise
            raise provider_error from exc
        return dedupe_by_period(result.get("filings", []), limit)

    def download_pdf(self, filing: Filing) -> bytes | None:
        """Render ``filing`` as PDF; ``None`` if it has no usable URL or rendering fails."""
        url = filing.get("linkToFilingDetails") or filing.get("linkToHtml")
        if not url:
            return None
        try:
            content: bytes | None = retry(lambda: self._pdf.get_pdf(url), description=f"SEC PDF {url}")
        except Exception as exc:
            provider_error = _classify(exc)
            if provider_error is None:
                raise
            raise provider_error from exc
        if not content or not content.startswith(b"%PDF"):
            logger.warning("SEC PDF endpoint returned no PDF for %s", url)
            return None
        return content
