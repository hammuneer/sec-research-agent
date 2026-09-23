"""Prompt settings page: edit the report prompts and manage saved versions."""

from __future__ import annotations

import streamlit as st

from ...reports.prompt_store import PromptVersion
from ...reports.prompts import PromptSet, default_prompts
from .. import components as ui

_GEN_KEY = "prompt_generation"
_FMT_KEY = "prompt_format"
_PENDING_KEY = "prompt_pending"


def _load_into_editor(prompts: PromptSet) -> None:
    """Queue values for the text areas (applied before widgets are created on the next run)."""
    st.session_state[_PENDING_KEY] = prompts


def _apply_pending() -> None:
    pending: PromptSet | None = st.session_state.pop(_PENDING_KEY, None)
    if pending is None and _GEN_KEY in st.session_state:
        return
    prompts = pending or ui.prompt_store().active()
    st.session_state[_GEN_KEY] = prompts.report_generation
    st.session_state[_FMT_KEY] = prompts.report_format


def _history(active: PromptVersion | None) -> None:
    store = ui.prompt_store()
    versions = store.history()
    if not versions:
        st.caption("No saved versions yet.")
        return
    for idx, version in enumerate(versions):
        is_active = active is not None and version == active
        title = f"{'● ' if is_active else ''}{version.label} · {version.timestamp}"
        with st.expander(title):
            if st.button("Use this version", key=f"use-{idx}", disabled=is_active):
                store.activate(version)
                _load_into_editor(version.prompts)
                st.rerun()
            st.text_area(
                "Report prompt", version.report_generation, height=120, disabled=True, key=f"g-{idx}"
            )
            st.text_area("Editing prompt", version.report_format, height=100, disabled=True, key=f"f-{idx}")


def render() -> None:
    """Render the prompt settings page."""
    ui.page_header("Prompt settings", "Customise how reports are written. Changes apply to the next report.")
    _apply_pending()
    store = ui.prompt_store()
    active = store.active_version()
    st.caption(f"Active: **{active.label}**" if active else "Active: **built-in defaults**")

    left, right = st.columns([2, 1], gap="large")
    with left:
        generation = st.text_area("Report generation prompt", key=_GEN_KEY, height=360)
        formatting = st.text_area("Report editing prompt", key=_FMT_KEY, height=240)
        label = st.text_input("Version label", placeholder="e.g. Shorter report")
        save_col, reset_col = st.columns(2)
        with save_col:
            if st.button("Save as new version", type="primary", width="stretch"):
                saved = store.save(PromptSet(generation, formatting), label)
                _load_into_editor(saved.prompts)
                st.toast(f"Saved '{saved.label}'")
                st.rerun()
        with reset_col:
            if st.button("Restore defaults", width="stretch", disabled=active is None):
                store.reset()
                _load_into_editor(default_prompts())
                st.rerun()
    with right:
        st.markdown("**Version history**")
        _history(active)
