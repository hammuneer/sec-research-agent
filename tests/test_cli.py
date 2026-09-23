"""Tests for :mod:`sec_research_agent.cli` (the runner and settings are faked/mocked; no
network, and the real project ``.env`` is never read).
"""

from __future__ import annotations

import pytest

from sec_research_agent import cli as cli_module
from sec_research_agent.pipeline.runner import RunProgress, Stage, TickerStatus
from sec_research_agent.storage import InvalidTickerError


def test_build_parser_requires_at_least_one_ticker() -> None:
    parser = cli_module.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_parses_tickers_and_report_flag() -> None:
    args = cli_module.build_parser().parse_args(["AAPL", "MSFT", "--report"])
    assert args.tickers == ["AAPL", "MSFT"]
    assert args.report is True


def test_build_parser_report_defaults_to_false() -> None:
    args = cli_module.build_parser().parse_args(["AAPL"])
    assert args.report is False


class _FakeRunner:
    """Stand-in for :class:`ResearchRunner` driven by a scripted per-ticker outcome."""

    def __init__(self, settings) -> None:
        self.settings = settings

    def new_progress(self, tickers, report_tickers=()):
        symbols = list(tickers)
        if not symbols:
            raise ValueError("Enter at least one ticker")
        for t in symbols:
            if t == "BAD SYMBOL":
                raise InvalidTickerError(f"Invalid ticker symbol: {t!r}")
        return RunProgress(
            tickers={t: TickerStatus(t, want_report=t in set(report_tickers)) for t in symbols}
        )

    def run(self, progress: RunProgress) -> RunProgress:
        for ticker, status in progress.tickers.items():
            outcome = self.behavior.get(ticker, "done")  # type: ignore[attr-defined]
            if outcome == "fatal":
                progress.finish(fatal_error="everything exploded")
                return progress
            status.stage = Stage.DONE if outcome == "done" else Stage.FAILED
            if outcome == "failed":
                status.error = "simulated failure"
        progress.finish()
        return progress


def _install_fake_runner(monkeypatch: pytest.MonkeyPatch, behavior: dict[str, str], settings_factory) -> None:
    def factory(settings):
        runner = _FakeRunner(settings)
        runner.behavior = behavior
        return runner

    monkeypatch.setattr(cli_module, "ResearchRunner", factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings_factory())
    monkeypatch.setattr(cli_module, "configure_logging", lambda level: None)


def test_main_returns_zero_when_all_tickers_succeed(
    monkeypatch: pytest.MonkeyPatch, settings_factory
) -> None:
    _install_fake_runner(monkeypatch, {"AAPL": "done"}, settings_factory)
    assert cli_module.main(["AAPL"]) == 0


def test_main_returns_one_when_a_ticker_fails(monkeypatch: pytest.MonkeyPatch, settings_factory) -> None:
    _install_fake_runner(monkeypatch, {"AAPL": "failed"}, settings_factory)
    assert cli_module.main(["AAPL"]) == 1


def test_main_returns_one_on_fatal_error(monkeypatch: pytest.MonkeyPatch, settings_factory) -> None:
    _install_fake_runner(monkeypatch, {"AAPL": "fatal"}, settings_factory)
    assert cli_module.main(["AAPL"]) == 1


def test_main_returns_two_for_invalid_ticker(monkeypatch: pytest.MonkeyPatch, settings_factory) -> None:
    _install_fake_runner(monkeypatch, {}, settings_factory)
    assert cli_module.main(["BAD SYMBOL"]) == 2
