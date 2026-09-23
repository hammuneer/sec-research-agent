"""Tests for :mod:`sec_research_agent.reports.prompt_store`."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from sec_research_agent.reports import prompt_store as prompt_store_module
from sec_research_agent.reports.prompt_store import PromptStore
from sec_research_agent.reports.prompts import PromptSet, default_prompts


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "settings" / "report_prompts.json"


def test_active_is_defaults_when_no_file_exists(path: Path) -> None:
    store = PromptStore(path)
    assert store.active() == default_prompts()
    assert store.active_version() is None
    assert store.history() == []


def test_save_becomes_active_and_appears_in_history(path: Path) -> None:
    store = PromptStore(path)
    prompts = PromptSet(report_generation="gen v1", report_format="fmt v1")
    version = store.save(prompts, label="My Version")

    assert store.active() == prompts
    active_version = store.active_version()
    assert active_version is not None
    assert active_version.label == "My Version"
    assert store.history() == [version]


def test_save_without_label_gets_a_timestamped_default_label(path: Path) -> None:
    store = PromptStore(path)
    version = store.save(PromptSet("g", "f"))
    assert version.label.startswith("Version ")


def test_multiple_saves_ordered_newest_first(path: Path) -> None:
    store = PromptStore(path)
    v1 = store.save(PromptSet("g1", "f1"), label="v1")
    v2 = store.save(PromptSet("g2", "f2"), label="v2")
    history = store.history()
    assert history[0].label == "v2"
    assert history[1].label == "v1"
    assert store.active_version() == v2
    assert v1 != v2


def test_history_is_capped_at_max_history(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_store_module, "MAX_HISTORY", 3)
    store = PromptStore(path)
    for i in range(5):
        store.save(PromptSet(f"g{i}", f"f{i}"), label=f"v{i}")
    assert len(store.history()) == 3
    assert store.history()[0].label == "v4"  # newest kept


def test_activate_switches_active_without_dropping_history(path: Path) -> None:
    store = PromptStore(path)
    v1 = store.save(PromptSet("g1", "f1"), label="v1")
    store.save(PromptSet("g2", "f2"), label="v2")
    store.activate(v1)
    assert store.active_version().label == "v1"
    assert len(store.history()) == 2  # history untouched


def test_reset_restores_defaults_but_keeps_history(path: Path) -> None:
    store = PromptStore(path)
    store.save(PromptSet("g1", "f1"), label="v1")
    store.reset()
    assert store.active() == default_prompts()
    assert store.active_version() is None
    assert len(store.history()) == 1


def test_corrupt_json_file_falls_back_to_defaults(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json at all", encoding="utf-8")
    store = PromptStore(path)
    assert store.active() == default_prompts()
    assert store.history() == []


def test_non_dict_entries_in_history_are_skipped(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"history": ["not-a-dict", {"label": "ok"}]}), encoding="utf-8")
    store = PromptStore(path)
    # The malformed entry (missing required keys) is dropped, not raised.
    assert store.history() == []


def test_save_is_atomic_write(path: Path) -> None:
    store = PromptStore(path)
    store.save(PromptSet("g", "f"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "active" in data and "history" in data


def test_concurrent_saves_do_not_corrupt_the_file(path: Path) -> None:
    store = PromptStore(path)

    def worker(i: int) -> None:
        store.save(PromptSet(f"g{i}", f"f{i}"), label=f"v{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The file must always be valid JSON with a consistent shape after concurrent writers.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "active" in data
    assert len(data["history"]) == 10
