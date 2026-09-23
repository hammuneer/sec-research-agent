"""Shared exception type for expected, actionable provider failures.

``sec.py`` and ``earnings.py`` translate the exceptions raised by their underlying HTTP
clients into :class:`ProviderError` so callers (:mod:`..pipeline.collect`) can log one clean
line instead of a full traceback, and can tell a failure that will keep happening (bad API
key, quota exhausted) from one that is specific to a single request (not found).
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """An external data provider rejected a request for an expected, actionable reason.

    Covers authentication failures, exhausted quotas, sustained rate limiting and
    not-found responses - the failure modes an operator can act on (fix a key, upgrade a
    plan, wait) as opposed to a bug in this codebase.
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        """Create the error.

        Args:
            message: A single-line, actionable description shown to the operator.
            fatal: True when the same failure will recur for every subsequent call to this
                provider within the run (rejected API key, exhausted quota), so the caller
                should stop calling it instead of retrying per document.
        """
        super().__init__(message)
        self.fatal = fatal
