# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-23

Initial public release.

### Added

- Collect 10-K, 10-Q and DEF 14A proxy filings via [sec-api.io](https://sec-api.io) and
  earnings-call transcripts via [earningscall.biz](https://earningscall.biz), stored locally
  as PDFs under `data/<TICKER>/{10-K,10-Q,Proxy,EarningsCalls}/`.
- Optional AI-written equity research report per ticker: PDF text extraction, chunking,
  OpenAI embeddings, RAG-based topic extraction, a GPT drafting pass and a GPT formatting
  pass, exported as Markdown, DOCX and PDF with a per-run token-cost JSON.
  under `data/<TICKER>/reports/`.
- Streamlit UI (`app.py`) with Research, Library and Prompt settings pages.
- `sec-research` CLI for headless / scripted runs.
- Configurable models, document limits, concurrency and pricing via `config/settings.yaml`.
- `docs/architecture.md` and `docs/configuration.md`.

### Fixed

- Expected provider failures (rejected API key, exhausted quota, rate limiting, not found) now
  log one clean, actionable line instead of a full traceback; a fatal failure (bad key,
  exhausted quota) disables that provider for the rest of the run instead of retrying it for
  every remaining document type and ticker.
- The embedding cache (`data/.cache/embeddings/`) is now written atomically and tolerates a
  truncated/corrupt cache file (e.g. from a concurrent write) by falling back to a cache miss
  instead of crashing the report.

[0.1.0]: https://github.com/hammuneer/sec-research-agent/releases/tag/v0.1.0
