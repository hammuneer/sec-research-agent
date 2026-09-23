"""Multi-ticker orchestration with a thread-safe progress object the UI can poll.

Stage 1 collects documents for all tickers in parallel. As soon as a ticker's documents are
in place, its report (if requested) is queued on a smaller pool so LLM rate limits are respected.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from ..config import AppConfig, Settings, get_config, get_settings
from ..sources.earnings import EarningsCallClient
from ..sources.sec import SecFilingsClient
from ..storage import LocalDocumentStore, normalize_ticker
from .collect import CollectionResult, DocumentCollector
from .report import ReportPipeline, ReportResult

logger = logging.getLogger(__name__)

MAX_TICKERS_PER_RUN = 50


class Stage(StrEnum):
    """Lifecycle of one ticker within a run."""

    PENDING = "Pending"
    COLLECTING = "Collecting"
    COLLECTED = "Collected"
    REPORT_QUEUED = "Report queued"
    REPORTING = "Writing report"
    DONE = "Done"
    FAILED = "Failed"


@dataclass
class TickerStatus:
    """Progress for a single ticker."""

    ticker: str
    want_report: bool
    stage: Stage = Stage.PENDING
    message: str = "Waiting"
    collection: CollectionResult | None = None
    report: ReportResult | None = None
    error: str | None = None

    @property
    def finished(self) -> bool:
        """True once no more work will happen for this ticker."""
        return self.stage in (Stage.DONE, Stage.FAILED)


@dataclass
class RunProgress:
    """Shared, lock-protected progress for one run. Read it through :meth:`snapshot`."""

    tickers: dict[str, TickerStatus] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    fatal_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, ticker: str, **changes: object) -> None:
        """Atomically update fields of one ticker's status."""
        with self._lock:
            status = self.tickers[ticker]
            for name, value in changes.items():
                setattr(status, name, value)

    def finish(self, fatal_error: str | None = None) -> None:
        """Mark the run as complete."""
        with self._lock:
            self.finished_at = time.time()
            self.fatal_error = fatal_error

    def snapshot(self) -> RunProgress:
        """Consistent deep copy safe to read without the lock."""
        with self._lock:
            return RunProgress(
                tickers=copy.deepcopy(self.tickers),
                started_at=self.started_at,
                finished_at=self.finished_at,
                fatal_error=self.fatal_error,
            )

    @property
    def done(self) -> bool:
        """True once the run has finished."""
        return self.finished_at is not None

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since the run started (frozen once finished)."""
        return (self.finished_at or time.time()) - self.started_at

    def count(self, *stages: Stage) -> int:
        """Number of tickers currently in any of ``stages``."""
        return sum(1 for s in self.tickers.values() if s.stage in stages)


def parse_tickers(raw: Iterable[str]) -> list[str]:
    """Normalise, validate and de-duplicate tickers (order preserved)."""
    seen: dict[str, None] = {}
    for item in raw:
        if item and item.strip():
            seen.setdefault(normalize_ticker(item), None)
    return list(seen)


class ResearchRunner:
    """Collect documents and optionally generate reports for many tickers."""

    def __init__(self, settings: Settings | None = None, config: AppConfig | None = None) -> None:
        self.settings = settings or get_settings()
        self.config = config or get_config()
        self.store = LocalDocumentStore(self.settings.data_dir)

    def _collector(self) -> DocumentCollector:
        s = self.settings
        sec = SecFilingsClient(s.sec_api_key.get_secret_value()) if s.sec_api_key else None
        earnings = (
            EarningsCallClient(s.earningscall_api_key.get_secret_value()) if s.earningscall_api_key else None
        )
        return DocumentCollector(self.store, self.config.documents, sec, earnings)

    def start(
        self, tickers: Iterable[str], report_tickers: Iterable[str] = ()
    ) -> tuple[RunProgress, threading.Thread]:
        """Validate input, then run in a daemon thread. Returns the live progress and the thread."""
        progress = self.new_progress(tickers, report_tickers)
        thread = threading.Thread(target=self.run, args=(progress,), name="research-run", daemon=True)
        thread.start()
        return progress, thread

    @staticmethod
    def new_progress(tickers: Iterable[str], report_tickers: Iterable[str] = ()) -> RunProgress:
        """Build the progress object for a run (validates tickers)."""
        symbols = parse_tickers(tickers)
        if not symbols:
            raise ValueError("Enter at least one ticker")
        if len(symbols) > MAX_TICKERS_PER_RUN:
            raise ValueError(f"At most {MAX_TICKERS_PER_RUN} tickers per run")
        wanted = set(parse_tickers(report_tickers))
        return RunProgress(tickers={t: TickerStatus(t, want_report=t in wanted) for t in symbols})

    def run(self, progress: RunProgress) -> RunProgress:
        """Execute the run described by ``progress`` (blocking)."""
        try:
            self._run(progress)
        except Exception as exc:
            logger.exception("Run failed")
            progress.finish(fatal_error=str(exc))
        else:
            progress.finish()
        return progress

    def _run(self, progress: RunProgress) -> None:
        tickers = list(progress.tickers)
        wants_reports = any(s.want_report for s in progress.tickers.values())
        collector = self._collector()
        report_pipeline: ReportPipeline | None = None
        report_setup_error = ""
        if wants_reports:
            try:
                report_pipeline = ReportPipeline(self.settings, self.config, self.store)
            except ValueError as exc:
                report_setup_error = str(exc)
                logger.error("Reports disabled for this run: %s", exc)
        workers = self.config.concurrency

        report_futures: list[Future[None]] = []
        with ThreadPoolExecutor(workers.report_workers, thread_name_prefix="report") as report_pool:

            def collect(ticker: str) -> None:
                self._collect_one(collector, progress, ticker)
                status = progress.tickers[ticker]
                if status.stage is not Stage.COLLECTED:
                    return
                if status.want_report and report_pipeline is None:
                    error = f"Report skipped: {report_setup_error}"
                    progress.update(ticker, stage=Stage.FAILED, error=error, message=error)
                elif status.want_report and report_pipeline is not None:
                    progress.update(ticker, stage=Stage.REPORT_QUEUED, message="Waiting for a report slot")
                    report_futures.append(
                        report_pool.submit(self._report_one, report_pipeline, progress, ticker)
                    )
                else:
                    progress.update(ticker, stage=Stage.DONE)

            with ThreadPoolExecutor(workers.collection_workers, thread_name_prefix="collect") as collect_pool:
                for future in [collect_pool.submit(collect, t) for t in tickers]:
                    future.result()
            for future in list(report_futures):
                future.result()

    @staticmethod
    def _collect_one(collector: DocumentCollector, progress: RunProgress, ticker: str) -> None:
        progress.update(ticker, stage=Stage.COLLECTING, message="Starting")
        try:
            result = collector.collect(
                ticker, lambda step, msg: progress.update(ticker, message=f"{step}: {msg}")
            )
        except Exception as exc:
            logger.exception("[%s] Collection crashed", ticker)
            progress.update(ticker, stage=Stage.FAILED, error=str(exc), message="Collection failed")
            return
        if result.total_on_disk == 0:
            error = "; ".join(result.errors) or "No documents found"
            progress.update(ticker, stage=Stage.FAILED, collection=result, error=error, message=error)
            return
        summary = f"{result.total_downloaded} new, {result.total_on_disk} total"
        if result.errors:
            summary += f" ({len(result.errors)} warning(s))"
        progress.update(ticker, stage=Stage.COLLECTED, collection=result, message=summary)

    @staticmethod
    def _report_one(pipeline: ReportPipeline, progress: RunProgress, ticker: str) -> None:
        progress.update(ticker, stage=Stage.REPORTING, message="Starting report")
        try:
            result = pipeline.run(ticker, on_step=lambda msg: progress.update(ticker, message=msg))
        except Exception as exc:
            logger.exception("[%s] Report failed", ticker)
            progress.update(
                ticker, stage=Stage.FAILED, error=f"Report failed: {exc}", message="Report failed"
            )
            return
        cost = result.cost.get("total_cost_usd", 0.0)
        progress.update(ticker, stage=Stage.DONE, report=result, message=f"Report ready (est. ${cost:.2f})")
