"""Research page: enter tickers, run collection / reports, watch progress."""

from __future__ import annotations

from itertools import cycle

import pandas as pd
import streamlit as st

from ...models import DocType
from ...pipeline.runner import MAX_TICKERS_PER_RUN, RunProgress, Stage, TickerStatus
from ...storage import InvalidTickerError, normalize_ticker
from .. import components as ui
from . import library

_PROGRESS_KEY = "run_progress"
_EDITOR_KEY = "ticker_editor"


def _active_run() -> RunProgress | None:
    return st.session_state.get(_PROGRESS_KEY)


def _read_editor(frame: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    """Return (tickers, report_tickers, errors) from the editor rows."""
    tickers: list[str] = []
    report: list[str] = []
    errors: list[str] = []
    for row in frame.to_dict("records"):
        raw = str(row.get("Ticker") or "").strip()
        if not raw:
            continue
        try:
            ticker = normalize_ticker(raw)
        except InvalidTickerError:
            errors.append(raw)
            continue
        if ticker in tickers:
            continue
        tickers.append(ticker)
        if row.get("AI report"):
            report.append(ticker)
    return tickers, report, errors


def _input_form(disabled: bool) -> None:
    st.subheader("Companies")
    st.caption(
        "Add one ticker per row. Filings are saved under the data folder, one folder per company; "
        "anything already downloaded is reused. Tick **AI report** to write a research report."
    )
    initial = pd.DataFrame([{"Ticker": "", "AI report": True}])
    frame = st.data_editor(
        initial,
        key=_EDITOR_KEY,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        disabled=disabled,
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", help="e.g. AAPL, MSFT, BRK.B", max_chars=10),
            "AI report": st.column_config.CheckboxColumn("AI report", default=True),
        },
    )
    tickers, report_tickers, errors = _read_editor(frame)
    if errors:
        st.error("Invalid ticker(s): " + ", ".join(errors))
    if len(tickers) > MAX_TICKERS_PER_RUN:
        st.error(f"At most {MAX_TICKERS_PER_RUN} tickers per run.")

    can_start = bool(tickers) and not errors and len(tickers) <= MAX_TICKERS_PER_RUN and not disabled
    label = f"Run for {len(tickers)} compan{'y' if len(tickers) == 1 else 'ies'}" if tickers else "Run"
    if st.button(label, type="primary", disabled=not can_start, icon=":material/play_arrow:"):
        progress, _ = ui.runner().start(tickers, report_tickers)
        st.session_state[_PROGRESS_KEY] = progress
        st.rerun()


def _status_rows(progress: RunProgress) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for s in progress.tickers.values():
        collection = s.collection
        rows.append(
            {
                "Ticker": s.ticker,
                "Stage": s.stage.value,
                "Details": s.error or s.message,
                "New files": collection.total_downloaded if collection else None,
                "On disk": collection.total_on_disk if collection else None,
                "Report": "Yes" if s.want_report else "No",
            }
        )
    return rows


def _progress_view(progress: RunProgress) -> None:
    snap = progress.snapshot()
    total = len(snap.tickers)
    collected = total - snap.count(Stage.PENDING, Stage.COLLECTING)
    wanted = [s for s in snap.tickers.values() if s.want_report]
    reports_done = sum(1 for s in wanted if s.finished)

    cols = st.columns(4)
    cols[0].metric("Companies", total)
    cols[1].metric("Completed", snap.count(Stage.DONE))
    cols[2].metric("Failed", snap.count(Stage.FAILED))
    cols[3].metric("Elapsed", ui.format_duration(snap.elapsed_seconds))

    st.progress(collected / total if total else 0.0, text=f"Documents: {collected}/{total} companies")
    if wanted:
        st.progress(reports_done / len(wanted), text=f"Reports: {reports_done}/{len(wanted)}")
    st.dataframe(pd.DataFrame(_status_rows(snap)), hide_index=True, width="stretch")


@st.fragment(run_every=1.0)
def _live_progress() -> None:
    progress = _active_run()
    if progress is None:
        return
    _progress_view(progress)
    if progress.done:
        st.rerun()


def _report_downloads(status: TickerStatus) -> None:
    report = status.report
    if report is None:
        return
    st.markdown(f"**{status.ticker}** research report")
    cols = st.columns(3)
    for col, path in zip(cols, (report.files.pdf, report.files.docx, report.files.markdown), strict=True):
        with col:
            ui.download_button(path, key=f"run-{path}")


def _open_in_library_link(ticker: str) -> None:
    """Link to the Library page, pre-selecting ``ticker`` via a query param."""
    library_page = st.Page(library.render, url_path="library")
    st.page_link(
        library_page,
        label=f"Open {ticker} in Library",
        icon=":material/folder_open:",
        query_params={"ticker": ticker},
    )


def _summary(progress: RunProgress) -> None:
    snap = progress.snapshot()
    if snap.fatal_error:
        st.error(f"Run failed: {snap.fatal_error}")
    failed = snap.count(Stage.FAILED)
    if failed:
        st.warning(f"Finished with {failed} failure(s) in {ui.format_duration(snap.elapsed_seconds)}.")
    else:
        st.success(f"Finished in {ui.format_duration(snap.elapsed_seconds)}.")

    cols = st.columns(4)
    cols[0].metric("Companies", len(snap.tickers))
    cols[1].metric("Completed", snap.count(Stage.DONE))
    cols[2].metric("Failed", failed)
    cols[3].metric("Elapsed", ui.format_duration(snap.elapsed_seconds))

    rows = []
    for s in snap.tickers.values():
        row: dict[str, object] = {"Ticker": s.ticker, "Status": s.stage.value}
        for doc_type in DocType:
            row[doc_type.value] = s.collection.on_disk[doc_type] if s.collection else 0
        row["New"] = s.collection.total_downloaded if s.collection else 0
        row["Details"] = s.error or s.message
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    succeeded = [s for s in snap.tickers.values() if s.stage is not Stage.FAILED]
    if succeeded:
        st.markdown("**Open in Library**")
        link_cols = cycle(st.columns(min(4, len(succeeded))))
        for col, status in zip(link_cols, succeeded, strict=False):
            with col:
                _open_in_library_link(status.ticker)

    for status in snap.tickers.values():
        _report_downloads(status)

    if st.button("New run", icon=":material/restart_alt:"):
        st.session_state.pop(_PROGRESS_KEY, None)
        st.session_state.pop(_EDITOR_KEY, None)
        st.rerun()


def render() -> None:
    """Render the research page."""
    ui.page_header("Research", "Collect SEC filings and earnings calls, then generate AI research reports.")
    ui.api_key_warnings()

    progress = _active_run()
    running = progress is not None and not progress.done
    if progress is None:
        _input_form(disabled=False)
        return

    st.subheader("Progress")
    if running:
        _live_progress()
    else:
        _summary(progress)
