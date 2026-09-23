"""Tests for :mod:`sec_research_agent.llm` (no network; the OpenAI client is faked)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from sec_research_agent.llm import LLMClient, LLMError, _retry_after_seconds, create_openai_client


def test_create_openai_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_openai_client(None)


def test_create_openai_client_builds_a_real_client_without_a_network_call() -> None:
    client = create_openai_client("sk-fake")
    assert client.api_key == "sk-fake"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-mini", False),
        ("gpt-4o", False),
        ("gpt-5.4", True),
        ("gpt-5", True),
        ("o1-preview", True),
        ("o3-mini", True),
    ],
)
def test_uses_responses_api_routing(model: str, expected: bool) -> None:
    assert LLMClient(client=object(), model=model).uses_responses_api is expected


class _FakeChatCompletions:
    def __init__(self, content: str | None = "hello world") -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _FakeResponses:
    def __init__(self, text: str | None = "hello from gpt-5", status: str = "completed") -> None:
        self.text = text
        self.status = status
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=20, output_tokens=8, total_tokens=28)
        return SimpleNamespace(output_text=self.text, usage=usage, status=self.status)


def _client_with(chat=None, responses=None) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=chat or _FakeChatCompletions()),
        responses=responses or _FakeResponses(),
    )


def test_chat_completions_path_returns_content_and_usage() -> None:
    fake = _FakeChatCompletions(content="the answer")
    client = LLMClient(_client_with(chat=fake), model="gpt-4o-mini")
    response = client.chat("system", "user", max_tokens=100, temperature=0.5)
    assert response.content == "the answer"
    assert response.usage.model == "gpt-4o-mini"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert fake.calls[0]["max_tokens"] == 100
    assert fake.calls[0]["temperature"] == 0.5


def test_chat_completions_empty_response_raises_llm_error() -> None:
    fake = _FakeChatCompletions(content="   ")
    client = LLMClient(_client_with(chat=fake), model="gpt-4o-mini")
    with pytest.raises(LLMError):
        client.chat("system", "user")


def test_chat_completions_none_content_raises_llm_error() -> None:
    fake = _FakeChatCompletions(content=None)
    client = LLMClient(_client_with(chat=fake), model="gpt-4o-mini")
    with pytest.raises(LLMError):
        client.chat("system", "user")


def test_responses_api_path_used_for_gpt5() -> None:
    fake = _FakeResponses(text="reasoned answer")
    client = LLMClient(_client_with(responses=fake), model="gpt-5.4", reasoning_effort="high")
    response = client.chat("system", "user", max_tokens=100)
    assert response.content == "reasoned answer"
    assert response.usage.input_tokens == 20
    # Reasoning calls get headroom above the requested max_tokens.
    assert fake.calls[0]["max_output_tokens"] >= 40_000
    assert fake.calls[0]["reasoning"] == {"effort": "high"}


def test_responses_api_effort_none_uses_requested_tokens_and_temperature() -> None:
    fake = _FakeResponses(text="answer")
    client = LLMClient(_client_with(responses=fake), model="gpt-5.4", reasoning_effort="none")
    client.chat("system", "user", max_tokens=123, temperature=0.7)
    assert fake.calls[0]["max_output_tokens"] == 123
    assert fake.calls[0]["temperature"] == 0.7


def test_responses_api_empty_text_raises_llm_error() -> None:
    fake = _FakeResponses(text="")
    client = LLMClient(_client_with(responses=fake), model="gpt-5.4")
    with pytest.raises(LLMError):
        client.chat("system", "user")


def test_responses_api_warns_on_non_completed_status(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    fake = _FakeResponses(text="partial answer", status="incomplete")
    client = LLMClient(_client_with(responses=fake), model="gpt-5.4")
    with caplog.at_level(logging.WARNING):
        response = client.chat("system", "user")
    assert response.content == "partial answer"
    assert any("incomplete" in r.message for r in caplog.records)


def _rate_limit_error(message: str = "Rate limited, try again in 0.01s") -> openai.RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code=429, request=request)
    return openai.RateLimitError(message, response=response, body=None)


def test_rate_limit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("sec_research_agent.llm.time.sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    class _FlakyChatCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _rate_limit_error()
            message = SimpleNamespace(content="finally")
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    client = LLMClient(_client_with(chat=_FlakyChatCompletions()), model="gpt-4o-mini")
    response = client.chat("system", "user")
    assert response.content == "finally"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_rate_limit_exhausts_retries_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sec_research_agent.llm.time.sleep", lambda s: None)

    class _AlwaysRateLimited:
        def create(self, **kwargs):
            raise _rate_limit_error()

    client = LLMClient(_client_with(chat=_AlwaysRateLimited()), model="gpt-4o-mini")
    with pytest.raises(openai.RateLimitError):
        client.chat("system", "user")


def test_retry_after_seconds_parses_hint() -> None:
    assert _retry_after_seconds("Rate limited; try again in 7.9s") == pytest.approx(8.9)
    assert _retry_after_seconds("Please retry after 3s") == pytest.approx(4.0)


def test_retry_after_seconds_defaults_when_no_hint() -> None:
    assert _retry_after_seconds("some other error message") == 10.0
