"""Settings / About page: read-only view of configuration, never secret values."""

from __future__ import annotations

import streamlit as st

from .. import components as ui

_ALL_KEYS = ("OPENAI_API_KEY", "SEC_API_KEY", "EARNINGSCALL_API_KEY")


def _key_status() -> None:
    st.subheader("API keys")
    st.caption("Configured via `.env` or the environment. Values are never shown here.")
    missing = set(ui.settings().missing_keys())
    for name in _ALL_KEYS:
        ok = name not in missing
        icon = ":material/check_circle:" if ok else ":material/cancel:"
        st.markdown(f":{'green' if ok else 'red'}[{icon}] `{name}`")


def _model_config() -> None:
    st.subheader("Models")
    models = ui.config().models
    rows = {
        "Extraction": models.extraction,
        "Report writing": models.report,
        "Report formatting": models.formatting,
        "Embedding": models.embedding,
        "Reasoning effort": models.reasoning_effort or "default",
    }
    for label, value in rows.items():
        st.markdown(f"**{label}:** `{value}`")


def _limits() -> None:
    st.subheader("Document limits per company")
    docs = ui.config().documents
    cols = st.columns(4)
    cols[0].metric("10-K", docs.annual_reports)
    cols[1].metric("10-Q", docs.quarterly_reports)
    cols[2].metric("Proxy", docs.proxy_statements)
    cols[3].metric("Earnings calls", docs.earnings_calls)


def _paths() -> None:
    st.subheader("Paths")
    s = ui.settings()
    st.markdown(f"**Data directory:** `{s.data_dir}`")
    st.markdown(f"**Config file:** `{s.config_file}`")
    st.markdown(f"**Log level:** `{s.log_level}`")


def render() -> None:
    """Render the settings / about page."""
    ui.page_header("Settings", "Configuration for this deployment. Read-only here.")
    left, right = st.columns(2, gap="large")
    with left:
        _key_status()
        st.divider()
        _paths()
    with right:
        _model_config()
        st.divider()
        _limits()
    st.divider()
    st.caption(f"{ui.BRAND} · data stored locally under `{ui.settings().data_dir}`.")
