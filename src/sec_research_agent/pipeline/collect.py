"""Download a company's filings and transcripts into the local store (idempotent)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import DocumentLimits
from ..models import DocType
from ..sources.earnings import (
    EarningsCallClient,
    build_earnings_filename,
    existing_periods,
    render_transcript_pdf,
)
from ..sources.errors import ProviderError
from ..sources.sec import SecFilingsClient, build_sec_filename
from ..storage import LocalDocumentStore

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, str], None]


def _noop(step: str, message: str) -> None:
    """Default status callback."""


@dataclass
class CollectionResult:
    """Outcome of collecting documents for one ticker."""

    ticker: str
    downloaded: dict[DocType, int] = field(default_factory=lambda: dict.fromkeys(DocType, 0))
    on_disk: dict[DocType, int] = field(default_factory=lambda: dict.fromkeys(DocType, 0))
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def total_downloaded(self) -> int:
        """New files written during this run."""
        return sum(self.downloaded.values())

    @property
    def total_on_disk(self) -> int:
        """Files available locally after this run."""
        return sum(self.on_disk.values())


def limit_for(limits: DocumentLimits, doc_type: DocType) -> int:
    """Configured number of documents to keep for ``doc_type``."""
    return {
        DocType.TEN_K: limits.annual_reports,
        DocType.TEN_Q: limits.quarterly_reports,
        DocType.PROXY: limits.proxy_statements,
        DocType.EARNINGS_CALL: limits.earnings_calls,
    }[doc_type]


class DocumentCollector:
    """Fetches missing documents for a ticker. Existing local files are never re-downloaded.

    One instance is shared across every ticker in a run (see
    :class:`~sec_research_agent.pipeline.runner.ResearchRunner`). When a provider (sec-api or
    earningscall) fails with a fatal :class:`~sec_research_agent.sources.errors.ProviderError`
    - a rejected API key or an exhausted quota - that provider is disabled for the rest of the
    run: later calls return immediately instead of hitting an API that has already said no.
    """

    def __init__(
        self,
        store: LocalDocumentStore,
        limits: DocumentLimits,
        sec_client: SecFilingsClient | None,
        earnings_client: EarningsCallClient | None,
    ) -> None:
        self.store = store
        self.limits = limits
        self.sec = sec_client
        self.earnings = earnings_client
        self._lock = threading.Lock()
        self._sec_disabled_reason: str | None = None
        self._earnings_disabled_reason: str | None = None

    def collect(self, ticker: str, on_status: StatusCallback = _noop) -> CollectionResult:
        """Collect every document type for ``ticker``. Per-type failures are recorded, not raised."""
        started = time.perf_counter()
        result = CollectionResult(ticker=ticker)
        for doc_type in DocType:
            on_status(doc_type.value, f"Checking {doc_type.display_name.lower()}")
            try:
                if doc_type is DocType.EARNINGS_CALL:
                    result.downloaded[doc_type] = self._collect_earnings(ticker, on_status)
                else:
                    result.downloaded[doc_type] = self._collect_sec(ticker, doc_type, on_status)
            except ProviderError as exc:
                self._record_provider_error(ticker, doc_type, exc, result)
            except Exception as exc:
                logger.exception("[%s] %s collection failed", ticker, doc_type.value)
                result.errors.append(f"{doc_type.value}: {exc}")
            result.on_disk[doc_type] = len(self.store.list_documents(ticker, doc_type))
        result.elapsed_seconds = time.perf_counter() - started
        logger.info(
            "[%s] %d new, %d on disk in %.0fs",
            ticker,
            result.total_downloaded,
            result.total_on_disk,
            result.elapsed_seconds,
        )
        return result

    def _record_provider_error(
        self, ticker: str, doc_type: DocType, exc: ProviderError, result: CollectionResult
    ) -> None:
        """Log an expected provider failure as one clean line and record it on ``result``.

        The full traceback still goes to DEBUG, so it is available with ``LOG_LEVEL=DEBUG``
        without spamming normal output. Fatal failures disable the provider for the rest of
        the run (see :meth:`_disable_provider`).
        """
        logger.error("[%s] %s: %s", ticker, doc_type.value, exc)
        logger.debug("[%s] %s collection failed", ticker, doc_type.value, exc_info=True)
        result.errors.append(f"{doc_type.value}: {exc}")
        if exc.fatal:
            self._disable_provider(doc_type, str(exc))

    def _disable_provider(self, doc_type: DocType, reason: str) -> None:
        """Stop calling ``doc_type``'s provider for the remainder of the run.

        Only the first reason is kept: once disabled, later calls short-circuit with a
        "skipped" :class:`ProviderError` of their own, which would otherwise overwrite the
        original message with an ever-growing "skipped - skipped - ..." chain.
        """
        with self._lock:
            if doc_type is DocType.EARNINGS_CALL:
                self._earnings_disabled_reason = self._earnings_disabled_reason or reason
            else:
                self._sec_disabled_reason = self._sec_disabled_reason or reason

    def _collect_sec(self, ticker: str, doc_type: DocType, on_status: StatusCallback) -> int:
        limit = limit_for(self.limits, doc_type)
        existing = self.store.existing_filenames(ticker, doc_type)
        if limit == 0 or len(existing) >= limit:
            return 0
        if self.sec is None:
            raise RuntimeError("SEC_API_KEY is not configured")
        if self._sec_disabled_reason:
            raise ProviderError(f"skipped - {self._sec_disabled_reason}", fatal=True)

        on_status(doc_type.value, "Searching SEC filings")
        filings = self.sec.search(ticker, doc_type, limit)
        missing = [f for f in filings if build_sec_filename(ticker, f, doc_type) not in existing]
        saved = 0
        for n, filing in enumerate(missing, 1):
            filename = build_sec_filename(ticker, filing, doc_type)
            on_status(doc_type.value, f"Downloading {n}/{len(missing)}: {filename}")
            try:
                content = self.sec.download_pdf(filing)
            except ProviderError as exc:
                logger.error("[%s] Failed to download %s: %s", ticker, filename, exc)
                logger.debug("[%s] Failed to download %s", ticker, filename, exc_info=True)
                if exc.fatal:
                    self._disable_provider(doc_type, str(exc))
                    break
                continue
            except Exception as exc:
                logger.error("[%s] Failed to download %s: %s", ticker, filename, exc)
                continue
            if content:
                self.store.save_document(ticker, doc_type, filename, content)
                saved += 1
        return saved

    def _collect_earnings(self, ticker: str, on_status: StatusCallback) -> int:
        doc_type = DocType.EARNINGS_CALL
        limit = limit_for(self.limits, doc_type)
        if limit == 0:
            return 0
        if self.earnings is None:
            raise RuntimeError("EARNINGSCALL_API_KEY is not configured")
        if self._earnings_disabled_reason:
            raise ProviderError(f"skipped - {self._earnings_disabled_reason}", fatal=True)

        on_status(doc_type.value, "Looking up earnings calls")
        have = existing_periods(self.store.existing_filenames(ticker, doc_type))
        company, calls = self.earnings.recent_calls(ticker, limit)
        missing = [c for c in calls if c.period_key not in have]
        saved = 0
        for n, call in enumerate(missing, 1):
            filename = build_earnings_filename(ticker, call)
            on_status(doc_type.value, f"Transcript {n}/{len(missing)}: {call.period_key}")
            try:
                text = self.earnings.transcript(company, call)
            except ProviderError as exc:
                logger.error("[%s] Transcript %s failed: %s", ticker, call.period_key, exc)
                logger.debug("[%s] Transcript %s failed", ticker, call.period_key, exc_info=True)
                if exc.fatal:
                    self._disable_provider(doc_type, str(exc))
                    break
                continue
            except Exception as exc:
                logger.error("[%s] Transcript %s failed: %s", ticker, call.period_key, exc)
                continue
            if not text:
                logger.info("[%s] No transcript available for %s", ticker, call.period_key)
                continue
            title = f"{ticker} {call.period_key.replace('_', ' ')} earnings call ({call.call_date:%B %d, %Y})"
            self.store.save_document(ticker, doc_type, filename, render_transcript_pdf(text, title))
            saved += 1
        return saved
