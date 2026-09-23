"""Reusable Streamlit building blocks and cached singletons."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import streamlit as st

from ..config import AppConfig, Settings, get_config, get_settings
from ..pipeline.runner import ResearchRunner
from ..reports.prompt_store import PromptStore
from ..storage import LocalDocumentStore

APP_TITLE = "SEC Research Agent"
BRAND = "Involabs Financial Agent"

_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@st.cache_resource
def runner() -> ResearchRunner:
    """Process-wide runner."""
    return ResearchRunner(get_settings(), get_config())


def settings() -> Settings:
    """Current settings."""
    return get_settings()


def config() -> AppConfig:
    """Current app config."""
    return get_config()


def store() -> LocalDocumentStore:
    """Local document store for the configured data directory."""
    return LocalDocumentStore(settings().data_dir)


def prompt_store() -> PromptStore:
    """Prompt override store."""
    return PromptStore(settings().prompt_store_path)


def page_header(title: str, subtitle: str) -> None:
    """Standard page title block."""
    st.title(title)
    st.caption(subtitle)


def api_key_warnings() -> None:
    """Warn about missing API keys."""
    missing = settings().missing_keys()
    if missing:
        st.warning(
            "Missing API keys: " + ", ".join(f"`{k}`" for k in missing) + ". Add them to `.env` and restart.",
            icon=":material/key_off:",
        )


def human_size(num_bytes: int) -> str:
    """Format a byte count, e.g. ``1.2 MB``."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def mime_type(path: Path) -> str:
    """MIME type for a download button."""
    return (
        _MIME_OVERRIDES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )


def download_button(path: Path, label: str | None = None, key: str | None = None) -> None:
    """Download button for a local file (skips silently if the file vanished)."""
    if not path.is_file():
        return
    st.download_button(
        label or path.suffix.lstrip(".").upper(),
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime_type(path),
        key=key or f"dl-{path}",
        width="stretch",
    )


def format_duration(seconds: float) -> str:
    """``125`` -> ``2m 05s``."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"
