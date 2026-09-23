"""Logging setup shared by the UI, CLI and library code."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False

# Third-party loggers that are noisy at INFO level.
_QUIET_LOGGERS = ("httpx", "httpcore", "openai", "urllib3", "yfinance", "peewee")


def configure_logging(level: str | int = "INFO") -> None:
    """Install a single rich console handler on the root logger. Safe to call repeatedly."""
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)
    if _CONFIGURED:
        return
    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s", datefmt="[%X]"))
    root.addHandler(handler)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _CONFIGURED = True
