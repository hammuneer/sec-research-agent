"""Streamlit entry point. Run with ``streamlit run app.py``."""

from __future__ import annotations

import streamlit as st

from ..config import get_settings
from ..logging import configure_logging
from .components import APP_TITLE, BRAND
from .pages import library, prompts, research, settings


def main() -> None:
    """Configure the page and dispatch to the selected view."""
    configure_logging(get_settings().log_level)
    st.set_page_config(page_title=APP_TITLE, page_icon=":material/query_stats:", layout="wide")

    pages = [
        st.Page(
            research.render,
            title="Research",
            icon=":material/travel_explore:",
            url_path="research",
            default=True,
        ),
        st.Page(library.render, title="Library", icon=":material/folder_open:", url_path="library"),
        st.Page(prompts.render, title="Prompt settings", icon=":material/tune:", url_path="prompts"),
        st.Page(settings.render, title="Settings", icon=":material/settings:", url_path="settings"),
    ]
    with st.sidebar:
        st.markdown(f"### {APP_TITLE}")
        st.caption(f"by {BRAND}")
    st.navigation(pages).run()
