"""Tests for :mod:`sec_research_agent.config`.

All :class:`Settings` are built with ``_env_file=None`` so the project's real ``.env`` is
never read from disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_research_agent.config import PROJECT_ROOT, AppConfig, Settings


def test_project_root_contains_pyproject() -> None:
    assert (PROJECT_ROOT / "pyproject.toml").exists()


def test_relative_paths_anchored_at_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.config_file == PROJECT_ROOT / "config" / "settings.yaml"
    # Paths are resolved independent of the current working directory.
    assert settings.data_dir.is_absolute()


def test_absolute_paths_left_untouched(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "elsewhere")  # type: ignore[call-arg]
    assert settings.data_dir == tmp_path / "elsewhere"


def test_derived_paths(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]
    assert settings.cache_dir == tmp_path / ".cache"
    assert settings.prompt_store_path == tmp_path / ".settings" / "report_prompts.json"


def test_missing_keys_all_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "SEC_API_KEY", "EARNINGSCALL_API_KEY", "EARNING_CALL_API"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert set(settings.missing_keys()) == {"OPENAI_API_KEY", "SEC_API_KEY", "EARNINGSCALL_API_KEY"}


def test_missing_keys_none_absent() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openai_api_key="sk-test",
        sec_api_key="sec-test",
        earningscall_api_key="ec-test",
    )
    assert settings.missing_keys() == []


def test_earningscall_key_accepts_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EARNINGSCALL_API_KEY", raising=False)
    monkeypatch.setenv("EARNING_CALL_API", "legacy-value")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.earningscall_api_key is not None
    assert settings.earningscall_api_key.get_secret_value() == "legacy-value"


def test_app_config_defaults_when_file_missing(tmp_path: Path) -> None:
    config = AppConfig.from_yaml(tmp_path / "nonexistent.yaml")
    assert config.models.report == "gpt-5.4"
    assert config.documents.annual_reports == 5
    assert config.pricing == {}


def test_app_config_loads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        """
models:
  report: gpt-4o
documents:
  annual_reports: 2
pricing:
  gpt-4o: { input: 1.0, output: 2.0 }
""",
        encoding="utf-8",
    )
    config = AppConfig.from_yaml(path)
    assert config.models.report == "gpt-4o"
    assert config.documents.annual_reports == 2
    assert config.pricing["gpt-4o"].input == 1.0


def test_app_config_letterhead_image_resolved_to_project_root(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("report:\n  letterhead_image: assets/letterhead.png\n", encoding="utf-8")
    config = AppConfig.from_yaml(path)
    assert config.report.letterhead_image == PROJECT_ROOT / "assets" / "letterhead.png"


def test_document_limits_bounds_enforced() -> None:
    from pydantic import ValidationError

    from sec_research_agent.config import DocumentLimits

    with pytest.raises(ValidationError):
        DocumentLimits(annual_reports=100)
