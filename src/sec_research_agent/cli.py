"""Command-line entry point: ``sec-research AAPL MSFT --report``."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import get_settings
from .logging import configure_logging
from .pipeline.runner import ResearchRunner, Stage
from .storage import InvalidTickerError

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="sec-research",
        description="Download SEC filings and earnings calls locally; optionally write AI research reports.",
    )
    parser.add_argument("tickers", nargs="+", help="Ticker symbols, e.g. AAPL MSFT")
    parser.add_argument(
        "--report", action="store_true", help="Also generate an AI research report per ticker"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    runner = ResearchRunner(settings)
    try:
        progress = runner.new_progress(args.tickers, args.tickers if args.report else ())
    except (InvalidTickerError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    runner.run(progress)
    if progress.fatal_error:
        logger.error("Run failed: %s", progress.fatal_error)
        return 1
    for status in progress.tickers.values():
        logger.info("%-8s %-10s %s", status.ticker, status.stage.value, status.error or status.message)
    return 1 if progress.count(Stage.FAILED) else 0


if __name__ == "__main__":
    sys.exit(main())
