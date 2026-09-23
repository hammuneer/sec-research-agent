"""Shared pytest fixtures.

Tests must never touch the real project ``.env`` or hit any network / paid API. Settings are
always built with ``_env_file=None`` and explicit keyword arguments so the project's real
``.env`` is never read.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sec_research_agent.config import AppConfig, Settings
from sec_research_agent.sources.earnings import render_transcript_pdf
from sec_research_agent.storage import LocalDocumentStore


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Empty local data directory for one test."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def store(data_dir: Path) -> LocalDocumentStore:
    """A fresh :class:`LocalDocumentStore` rooted at ``data_dir``."""
    return LocalDocumentStore(data_dir)


@pytest.fixture
def settings_factory(tmp_path: Path):
    """Factory building isolated :class:`Settings` that never read the real ``.env``."""

    def make(**overrides: object) -> Settings:
        kwargs: dict[str, object] = {
            "data_dir": tmp_path / "data",
            "config_file": tmp_path / "settings.yaml",
        }
        kwargs.update(overrides)
        return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]

    return make


@pytest.fixture
def app_config() -> AppConfig:
    """A default :class:`AppConfig` with small concurrency, for fast tests."""
    config = AppConfig()
    config.concurrency.collection_workers = 2
    config.concurrency.report_workers = 2
    config.concurrency.llm_workers = 2
    config.concurrency.pdf_workers = 2
    return config


def make_pdf_bytes(text: str, title: str = "Test Document") -> bytes:
    """A small, real, valid PDF containing ``text`` (built with reportlab, no external tools)."""
    return render_transcript_pdf(text, title)


def write_pdf(path: Path, text: str, title: str = "Test Document") -> Path:
    """Write a small real PDF containing ``text`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_pdf_bytes(text, title))
    return path


class FakeEmbeddingsAPI:
    """Stand-in for ``client.embeddings`` returning deterministic, cheap vectors."""

    def __init__(self, dim: int = 4, vector_for=None) -> None:
        self.dim = dim
        self._vector_for = vector_for
        self.calls: list[list[str]] = []

    def create(self, model: str, input: list[str]):
        self.calls.append(list(input))
        data = []
        for local_idx, text in enumerate(input):
            vector = self._vector_for(text, self.dim) if self._vector_for else _default_vector(text, self.dim)
            data.append(SimpleNamespace(embedding=vector, index=local_idx))
        usage = SimpleNamespace(prompt_tokens=len(input), total_tokens=len(input))
        return SimpleNamespace(data=data, usage=usage)


def _default_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    vec[hash(text) % dim] = 1.0
    return vec


class FakeChatCompletionsAPI:
    """Stand-in for ``client.chat.completions`` returning a fixed, deterministic reply."""

    def __init__(self, reply=None) -> None:
        self._reply = reply
        self.calls: list[dict] = []

    def create(self, model: str, messages: list[dict], max_tokens: int, temperature: float):
        self.calls.append({"model": model, "messages": messages, "max_tokens": max_tokens})
        content = self._reply(model, messages) if self._reply else f"[{model}] fake response"
        message = SimpleNamespace(content=content)
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeOpenAIClient:
    """Fake OpenAI client exposing only ``embeddings`` and ``chat.completions``.

    Every model used in tests is routed through Chat Completions (never the Responses API) by
    keeping test :class:`~sec_research_agent.config.ModelConfig` entries free of ``gpt-5``/``o1``
    prefixes, so this fake never needs to implement ``responses.create``.
    """

    def __init__(self, embed_dim: int = 4, vector_for=None, chat_reply=None) -> None:
        self.embeddings = FakeEmbeddingsAPI(embed_dim, vector_for)
        self.chat = SimpleNamespace(completions=FakeChatCompletionsAPI(chat_reply))
