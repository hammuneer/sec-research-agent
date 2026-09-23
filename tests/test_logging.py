"""Tests for :mod:`sec_research_agent.logging`."""

from __future__ import annotations

import logging

import pytest
from rich.logging import RichHandler

from sec_research_agent import logging as logging_module
from sec_research_agent.logging import _QUIET_LOGGERS, configure_logging


def _rich_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, RichHandler)]


@pytest.fixture(autouse=True)
def _isolated_root_logger():
    """Configuring logging mutates process-wide state; save and restore it around each test."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_configured = logging_module._CONFIGURED
    for h in original_handlers:
        root.removeHandler(h)
    logging_module._CONFIGURED = False
    try:
        yield
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in original_handlers:
            root.addHandler(h)
        root.setLevel(original_level)
        logging_module._CONFIGURED = original_configured


def test_configure_logging_installs_one_rich_handler() -> None:
    configure_logging("INFO")
    assert len(_rich_handlers()) == 1
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")
    # The handler is only installed once, but the level is still updated on every call.
    assert len(_rich_handlers()) == 1
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_quiets_noisy_third_party_loggers() -> None:
    configure_logging("DEBUG")
    for name in _QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_configure_logging_accepts_string_and_int_levels() -> None:
    configure_logging(logging.WARNING)
    assert logging.getLogger().level == logging.WARNING
