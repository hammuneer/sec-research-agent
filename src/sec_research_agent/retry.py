"""Small retry helper for flaky third-party APIs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_timeout_error(exc: BaseException) -> bool:
    """Heuristic: does ``exc`` look like a transient timeout / connection problem?"""
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    message = str(exc).lower()
    return any(s in message for s in ("timeout", "timed out", "temporarily unavailable", "connection reset"))


def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    should_retry: Callable[[BaseException], bool] = is_timeout_error,
    description: str = "call",
) -> T:
    """Call ``fn`` and retry with exponential backoff while ``should_retry(exc)`` is true."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not should_retry(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning("%s failed (%s); retry %d/%d in %.0fs", description, exc, attempt, attempts, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
