"""Library page: browse companies, then drill into one to see docs and reports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ...models import DocType
from .. import components as ui

_SELECTED_KEY = "library_selected_ticker"


def _file_table(paths: list[Path]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"File": p.name, "Size": ui.human_size(p.stat().st_size)} for p in paths if p.is_file()]
    )


def _latest_mtime(paths: list[Path]) -> str:
    files = [p for p in paths if p.is_file()]
    if not files:
        return "—"
    newest = max(p.stat().st_mtime for p in files)
    return datetime.fromtimestamp(newest).strftime("%Y-%m-%d")


def _select_company(ticker: str) -> None:
    st.session_state[_SELECTED_KEY] = ticker


def _overview(companies: list[str]) -> None:
    """A card per company: doc counts by type, latest filing date, report status."""
    st.subheader("Companies")
    query_ticker = st.query_params.get("ticker")
    if query_ticker and query_ticker in companies:
        st.session_state[_SELECTED_KEY] = query_ticker

    cols_per_row = 3
    for row_start in range(0, len(companies), cols_per_row):
        row = companies[row_start : row_start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, ticker in zip(cols, row, strict=False):
            with col, st.container(border=True):
                documents = ui.store().all_documents(ticker)
                all_paths = [p for paths in documents.values() for p in paths]
                reports = ui.store().list_reports(ticker)
                has_report = any(p.suffix == ".md" for p in reports)

                st.markdown(f"#### {ticker}")
                if has_report:
                    st.badge("Report ready", icon=":material/check:", color="green")
                else:
                    st.badge("No report", icon=":material/hourglass_empty:", color="gray")
                doc_cols = st.columns(4)
                for dc, doc_type in zip(doc_cols, DocType, strict=True):
                    dc.metric(doc_type.value, len(documents[doc_type]))
                st.caption(f"Latest filing: {_latest_mtime(all_paths)}")
                st.button(
                    "Open",
                    key=f"open-{ticker}",
                    on_click=_select_company,
                    args=(ticker,),
                    width="stretch",
                    icon=":material/arrow_forward:",
                )


def _documents_section(ticker: str) -> None:
    documents = ui.store().all_documents(ticker)
    tabs = st.tabs([f"{t.value} ({len(documents[t])})" for t in DocType])
    for tab, doc_type in zip(tabs, DocType, strict=True):
        with tab:
            paths = documents[doc_type]
            if not paths:
                st.caption(f"No {doc_type.display_name.lower()} yet.")
                continue
            st.dataframe(_file_table(paths), hide_index=True, width="stretch")
            choice = st.selectbox(
                "Download", paths, format_func=lambda p: p.name, key=f"pick-{ticker}-{doc_type.value}"
            )
            if choice is not None:
                ui.download_button(choice, label=f"Download {choice.name}", key=f"doc-{choice}")


def _reports_section(ticker: str) -> None:
    reports = ui.store().list_reports(ticker)
    markdown = [p for p in reports if p.suffix == ".md"]
    if not markdown:
        st.caption("No reports yet. Run the research page with **AI report** ticked.")
        return
    for md_path in markdown:
        stem = md_path.stem
        with st.expander(stem, expanded=md_path == markdown[0]):
            cols = st.columns(3)
            for col, suffix in zip(cols, (".pdf", ".docx", ".md"), strict=True):
                with col:
                    ui.download_button(md_path.with_suffix(suffix), key=f"rep-{stem}{suffix}")
            st.markdown(md_path.read_text(encoding="utf-8"))


def _company_detail(ticker: str, companies: list[str]) -> None:
    back, title = st.columns([1, 5])
    with back:
        if st.button("← Back", key="lib-back"):
            st.session_state.pop(_SELECTED_KEY, None)
            st.query_params.clear()
            st.rerun()
    with title:
        st.subheader(ticker)
    if ticker not in companies:
        st.error(f"No data found for {ticker}.")
        return
    st.markdown("**Reports**")
    _reports_section(ticker)
    st.markdown("**Source documents**")
    _documents_section(ticker)


def render() -> None:
    """Render the library page."""
    ui.page_header("Library", f"Everything stored under `{ui.settings().data_dir}`.")
    companies = ui.store().list_companies()
    if not companies:
        st.info("No companies yet. Start a run on the Research page.", icon=":material/info:")
        return

    selected = st.session_state.get(_SELECTED_KEY)
    if selected:
        _company_detail(selected, companies)
    else:
        _overview(companies)
