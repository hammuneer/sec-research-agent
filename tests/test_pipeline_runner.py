"""Tests for :mod:`sec_research_agent.pipeline.runner` (collect/report pipelines are mocked)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from sec_research_agent.config import AppConfig
from sec_research_agent.pipeline import runner as runner_module
from sec_research_agent.pipeline.collect import CollectionResult
from sec_research_agent.pipeline.report import ReportResult
from sec_research_agent.pipeline.runner import (
    MAX_TICKERS_PER_RUN,
    ResearchRunner,
    RunProgress,
    Stage,
    TickerStatus,
    parse_tickers,
)
from sec_research_agent.storage import InvalidTickerError


def test_parse_tickers_normalizes_dedupes_and_skips_blank() -> None:
    assert parse_tickers(["aapl", " ", "AAPL", "", "msft"]) == ["AAPL", "MSFT"]


def test_parse_tickers_rejects_invalid_symbol() -> None:
    with pytest.raises(InvalidTickerError):
        parse_tickers(["not a ticker"])


def test_new_progress_requires_at_least_one_ticker() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ResearchRunner.new_progress([])


def test_new_progress_enforces_max_tickers() -> None:
    too_many = [f"T{i}" for i in range(MAX_TICKERS_PER_RUN + 1)]
    with pytest.raises(ValueError, match="At most"):
        ResearchRunner.new_progress(too_many)


def test_new_progress_marks_want_report_only_for_requested_tickers() -> None:
    progress = ResearchRunner.new_progress(["AAPL", "MSFT"], ["AAPL"])
    assert progress.tickers["AAPL"].want_report is True
    assert progress.tickers["MSFT"].want_report is False
    assert all(s.stage is Stage.PENDING for s in progress.tickers.values())


def test_run_progress_update_is_thread_safe() -> None:
    progress = RunProgress(tickers={"AAPL": TickerStatus("AAPL", want_report=False)})

    def hammer() -> None:
        for _ in range(200):
            progress.update("AAPL", message="x")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert progress.tickers["AAPL"].message == "x"


def test_run_progress_snapshot_is_an_independent_copy() -> None:
    progress = ResearchRunner.new_progress(["AAPL"])
    snap = progress.snapshot()
    progress.update("AAPL", message="changed")
    assert snap.tickers["AAPL"].message != "changed"


def test_run_progress_count() -> None:
    progress = ResearchRunner.new_progress(["AAPL", "MSFT", "GOOG"])
    progress.update("AAPL", stage=Stage.DONE)
    progress.update("MSFT", stage=Stage.FAILED)
    assert progress.count(Stage.DONE) == 1
    assert progress.count(Stage.DONE, Stage.FAILED) == 2
    assert progress.count(Stage.PENDING) == 1


@pytest.fixture
def runner(tmp_path: Path, app_config: AppConfig, settings_factory) -> ResearchRunner:
    settings = settings_factory()
    return ResearchRunner(settings=settings, config=app_config)


class _FakeCollector:
    def __init__(self, behavior) -> None:
        self.behavior = behavior  # ticker -> "ok" | "empty" | "raise"

    def collect(self, ticker: str, on_status=None) -> CollectionResult:
        outcome = self.behavior.get(ticker, "ok")
        if outcome == "raise":
            raise RuntimeError(f"collection exploded for {ticker}")
        result = CollectionResult(ticker=ticker)
        if outcome == "ok":
            result.downloaded[next(iter(result.downloaded))] = 1
            result.on_disk[next(iter(result.on_disk))] = 1
        return result


class _FakeReportPipeline:
    def __init__(self, behavior, *_args, **_kwargs) -> None:
        self.behavior = behavior

    def run(self, ticker: str, on_step=None) -> ReportResult:
        outcome = self.behavior.get(ticker, "ok")
        if outcome == "raise":
            raise RuntimeError(f"report exploded for {ticker}")
        return ReportResult(
            ticker=ticker,
            files=None,  # not inspected by the runner
            cost_path=Path("/dev/null"),
            cost={"total_cost_usd": 1.23},
            elapsed_seconds=0.01,
        )


def test_run_all_tickers_succeed_without_reports(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "ok", "MSFT": "ok"}))
    progress = runner.new_progress(["AAPL", "MSFT"])
    runner.run(progress)

    assert progress.done
    assert progress.fatal_error is None
    assert progress.tickers["AAPL"].stage is Stage.DONE
    assert progress.tickers["MSFT"].stage is Stage.DONE


def test_run_marks_ticker_failed_when_collection_raises(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "raise", "MSFT": "ok"}))
    progress = runner.new_progress(["AAPL", "MSFT"])
    runner.run(progress)

    assert progress.tickers["AAPL"].stage is Stage.FAILED
    assert "exploded" in progress.tickers["AAPL"].error
    # A crash for one ticker must not affect an unrelated ticker running in parallel.
    assert progress.tickers["MSFT"].stage is Stage.DONE


def test_run_marks_ticker_failed_when_collection_finds_nothing(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "empty"}))
    progress = runner.new_progress(["AAPL"])
    runner.run(progress)
    assert progress.tickers["AAPL"].stage is Stage.FAILED
    assert progress.tickers["AAPL"].error


def test_run_generates_report_for_requested_ticker(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "ok"}))
    monkeypatch.setattr(
        runner_module, "ReportPipeline", lambda *a, **k: _FakeReportPipeline({"AAPL": "ok"}, *a, **k)
    )
    progress = runner.new_progress(["AAPL"], ["AAPL"])
    runner.run(progress)

    status = progress.tickers["AAPL"]
    assert status.stage is Stage.DONE
    assert status.report is not None
    assert "1.23" in status.message


def test_run_marks_failed_when_report_generation_raises(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "ok"}))
    monkeypatch.setattr(
        runner_module, "ReportPipeline", lambda *a, **k: _FakeReportPipeline({"AAPL": "raise"}, *a, **k)
    )
    progress = runner.new_progress(["AAPL"], ["AAPL"])
    runner.run(progress)

    status = progress.tickers["AAPL"]
    assert status.stage is Stage.FAILED
    assert "Report failed" in status.error


def test_run_degrades_gracefully_when_report_pipeline_cannot_be_built(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a, **_k):
        raise ValueError("OPENAI_API_KEY is not configured")

    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "ok", "MSFT": "ok"}))
    monkeypatch.setattr(runner_module, "ReportPipeline", _boom)
    progress = runner.new_progress(["AAPL", "MSFT"], ["AAPL"])
    runner.run(progress)

    # AAPL wanted a report but the pipeline could not be constructed at all: fails clearly.
    assert progress.tickers["AAPL"].stage is Stage.FAILED
    assert "Report skipped" in progress.tickers["AAPL"].error
    # MSFT never asked for a report, so it still completes normally.
    assert progress.tickers["MSFT"].stage is Stage.DONE


def test_start_runs_in_background_thread_and_completes(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_collector", lambda: _FakeCollector({"AAPL": "ok"}))
    progress, thread = runner.start(["AAPL"])
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert progress.done
    assert progress.tickers["AAPL"].stage is Stage.DONE


def test_run_sets_fatal_error_on_unexpected_exception(
    runner: ResearchRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom():
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr(runner, "_collector", _boom)
    progress = runner.new_progress(["AAPL"])
    runner.run(progress)
    assert progress.fatal_error is not None
    assert "unexpected" in progress.fatal_error
