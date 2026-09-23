"""OpenAI chat client that picks the right API for the model family."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

import openai
from openai import OpenAI

from .models import LLMResponse, TokenUsage

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Models served through the Responses API with reasoning controls.
_RESPONSES_API_PREFIXES = ("gpt-5", "o1", "o3", "o4")
# Reasoning tokens count against the output budget, so reasoning calls need headroom.
_MIN_REASONING_OUTPUT_TOKENS = 40_000
_RETRY_AFTER = re.compile(r"(?:try again in|retry after)\s+([\d.]+)\s*s", re.IGNORECASE)


class LLMError(RuntimeError):
    """The model returned no usable output."""


def create_openai_client(api_key: str | None, *, max_retries: int = 4, timeout: float = 600.0) -> OpenAI:
    """OpenAI client with SDK-level retries for connection errors, timeouts, 429s and 5xx."""
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=api_key, max_retries=max_retries, timeout=timeout)


class LLMClient:
    """Chat wrapper: GPT-5 / o-series use the Responses API, everything else Chat Completions."""

    RATE_LIMIT_RETRIES = 5

    def __init__(self, client: OpenAI, model: str, reasoning_effort: str | None = None) -> None:
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.uses_responses_api = model.startswith(_RESPONSES_API_PREFIXES)

    def chat(self, system: str, user: str, max_tokens: int = 8192, temperature: float = 0.3) -> LLMResponse:
        """Send one system + user turn and return the text and token usage."""
        if self.uses_responses_api:
            return self._via_responses(system, user, max_tokens, temperature)
        return self._via_chat_completions(system, user, max_tokens, temperature)

    # ------------------------------------------------------------------ internals

    def _with_rate_limit_backoff(self, fn: Callable[[], T]) -> T:
        """Retry on 429 beyond the SDK's own retries, honouring the server's suggested wait."""
        for attempt in range(1, self.RATE_LIMIT_RETRIES + 1):
            try:
                return fn()
            except openai.RateLimitError as exc:
                if attempt == self.RATE_LIMIT_RETRIES:
                    raise
                wait = _retry_after_seconds(str(exc))
                logger.warning(
                    "Rate limited on %s; waiting %.0fs (%d/%d)",
                    self.model,
                    wait,
                    attempt,
                    self.RATE_LIMIT_RETRIES,
                )
                time.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _via_responses(self, system: str, user: str, max_tokens: int, temperature: float) -> LLMResponse:
        effort = self.reasoning_effort or "high"
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "max_output_tokens": max(max_tokens, _MIN_REASONING_OUTPUT_TOKENS)
            if effort != "none"
            else max_tokens,
            "reasoning": {"effort": effort},
        }
        if effort == "none":
            kwargs["temperature"] = temperature

        resp = self._with_rate_limit_backoff(lambda: self.client.responses.create(**kwargs))
        if getattr(resp, "status", "completed") != "completed":
            logger.warning("%s response status is %r; output may be truncated", self.model, resp.status)
        text = resp.output_text or ""
        if not text.strip():
            raise LLMError(f"{self.model} returned an empty response")
        usage = resp.usage
        return LLMResponse(
            content=text,
            usage=TokenUsage(
                model=self.model,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
        )

    def _via_chat_completions(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> LLMResponse:
        resp = self._with_rate_limit_backoff(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )
        text = resp.choices[0].message.content if resp.choices else None
        if not text or not text.strip():
            raise LLMError(f"{self.model} returned an empty response")
        usage = resp.usage
        return LLMResponse(
            content=text,
            usage=TokenUsage(
                model=self.model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
        )


def _retry_after_seconds(message: str) -> float:
    """Parse "try again in 7.9s" style hints; default to 10s."""
    match = _RETRY_AFTER.search(message)
    return float(match.group(1)) + 1.0 if match else 10.0
