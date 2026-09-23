"""Tests for :mod:`sec_research_agent.retry`."""

from __future__ import annotations

import pytest

from sec_research_agent.retry import is_timeout_error, retry


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("boom"),
        ConnectionError("boom"),
        RuntimeError("Connection timed out"),
        RuntimeError("please try again, temporarily unavailable"),
        RuntimeError("connection reset by peer"),
        RuntimeError("Request TIMEOUT after 30s"),
    ],
)
def test_is_timeout_error_true_for_transient_failures(exc: BaseException) -> None:
    assert is_timeout_error(exc) is True


@pytest.mark.parametrize(
    "exc", [ValueError("bad input"), KeyError("missing"), RuntimeError("permission denied")]
)
def test_is_timeout_error_false_for_non_transient_failures(exc: BaseException) -> None:
    assert is_timeout_error(exc) is False


def test_retry_succeeds_on_first_attempt() -> None:
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert retry(fn, attempts=3, base_delay=0) == "ok"
    assert calls["n"] == 1


def test_retry_recovers_after_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("sec_research_agent.retry.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("flaky")
        return "recovered"

    result = retry(fn, attempts=5, base_delay=1.0)
    assert result == "recovered"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff: base_delay * 2**(attempt-1)


def test_retry_gives_up_after_exhausting_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sec_research_agent.retry.time.sleep", lambda s: None)

    def fn():
        raise TimeoutError("always fails")

    with pytest.raises(TimeoutError, match="always fails"):
        retry(fn, attempts=3, base_delay=0.01)


def test_retry_does_not_retry_when_should_retry_returns_false() -> None:
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("not transient")

    with pytest.raises(ValueError, match="not transient"):
        retry(fn, attempts=5, should_retry=lambda exc: False)
    assert calls["n"] == 1  # no retries attempted


def test_retry_uses_custom_should_retry_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sec_research_agent.retry.time.sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("retry me")
        return "done"

    result = retry(fn, attempts=3, should_retry=lambda exc: "retry me" in str(exc))
    assert result == "done"
    assert calls["n"] == 2
