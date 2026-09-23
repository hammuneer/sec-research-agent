# Configuration

`sec-research-agent` reads configuration from two places, both optional:

- **Environment variables / `.env`** (`sec_research_agent.config.Settings`) - secrets and
  filesystem paths.
- **`config/settings.yaml`** (`sec_research_agent.config.AppConfig`) - models, limits,
  concurrency, retrieval and pricing. Every key is optional; a missing file or a missing key
  falls back to the default shown below. Relative paths in either file are resolved against the
  project root (the directory containing `pyproject.toml`), so the app behaves the same
  regardless of the working directory it's started from.

Both are process-wide singletons (`get_settings()` / `get_config()`, `functools.lru_cache`), so
every component - CLI, UI, runner - sees the same values within one process.

## Environment variables (`.env`)

Copy [`.env.example`](../.env.example) to `.env` and fill in what you need.

| Variable | Type | Default | Effect |
|---|---|---|---|
| `OPENAI_API_KEY` | secret | unset | Required to generate reports (embeddings, topic extraction, drafting, polishing). Collection-only runs don't need it. |
| `SEC_API_KEY` | secret | unset | Required to collect 10-K / 10-Q / DEF 14A filings, via [sec-api.io](https://sec-api.io). Without it, those document types are skipped. |
| `EARNINGSCALL_API_KEY` | secret | unset | Required to collect earnings-call transcripts, via [earningscall.biz](https://earningscall.biz). Without it, that document type is skipped. Also accepted under the legacy alias `EARNING_CALL_API`. |
| `DATA_DIR` | path | `./data` | Root folder for all company data and generated reports (see [architecture.md](architecture.md#storage-layout)). |
| `CONFIG_FILE` | path | `./config/settings.yaml` | Alternative path to the YAML config file below. |
| `LOG_LEVEL` | string | `INFO` | Python logging level for the root logger (e.g. `DEBUG` to see full tracebacks for provider errors that are otherwise logged as one line). |

None of these are required just to start the app - the UI and CLI both run with zero keys
configured; they report which document types and report generation are unavailable (the UI's
`api_key_warnings()` banner; the CLI logs failures per ticker) rather than refusing to start.

## `config/settings.yaml`

The bundled [`config/settings.yaml`](../config/settings.yaml) documents itself with inline
comments; this table is the full reference with defaults from
`sec_research_agent.config.AppConfig`.

### `models`

| Key | Default | Effect |
|---|---|---|
| `models.extraction` | `gpt-4o-mini` | Model used for per-topic retrieval extraction (`rag/query_engine.py`). Routed through Chat Completions unless the name starts with `gpt-5`/`o1`/`o3`/`o4`. |
| `models.report` | `gpt-5.4` | Model used to draft the report (`reports/generator.py`). |
| `models.formatting` | `gpt-5.4` | Model used for the editorial polish pass. |
| `models.embedding` | `text-embedding-3-small` | Embedding model for retrieval (`rag/vector_store.py`); part of the embedding cache key, so changing it invalidates the cache. |
| `models.reasoning_effort` | `high` | GPT-5 family only (ignored by other models): `none`, `low`, `medium`, `high`, `xhigh`. Higher effort increases both quality and token cost; `none` also enables a `temperature` parameter that reasoning calls otherwise ignore. |

### `documents`

Maximum number of documents of each type kept per company (`pipeline/collect.py:limit_for`).
Setting one to `0` skips that document type entirely, with no network call.

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `documents.annual_reports` | `5` | 0-20 | Max 10-K filings |
| `documents.quarterly_reports` | `8` | 0-40 | Max 10-Q filings |
| `documents.proxy_statements` | `1` | 0-10 | Max DEF 14A filings |
| `documents.earnings_calls` | `8` | 0-40 | Max earnings-call transcripts |

### `concurrency`

Thread-pool sizes (`pipeline/runner.py`, `rag/query_engine.py`, `documents.py`); see
[architecture.md](architecture.md#concurrency-model) for how they interact.

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `concurrency.collection_workers` | `4` | 1-32 | Tickers collected in parallel |
| `concurrency.report_workers` | `2` | 1-16 | Reports generated in parallel |
| `concurrency.llm_workers` | `5` | 1-32 | Parallel topic extractions within one report |
| `concurrency.pdf_workers` | `8` | 1-64 | Parallel PDF text extraction within one report |

### `retrieval`

| Key | Default | Effect |
|---|---|---|
| `retrieval.chunk_size` | `4000` | Characters per chunk (`rag/chunker.py`), roughly 1,000 tokens. Part of the embedding cache key. |
| `retrieval.chunk_overlap` | `400` | Character overlap between consecutive chunks. Must be smaller than `chunk_size`. Part of the embedding cache key. |
| `retrieval.use_cache` | `true` | Reuse the on-disk embedding cache for an unchanged document set; `false` always re-embeds. |

### `report`

| Key | Default | Effect |
|---|---|---|
| `report.brand_name` | `Involabs Financial Agent` | Header text drawn on every exported `.docx`/`.pdf` page. |
| `report.letterhead_image` | `null` | Optional path to a full-width header image for the PDF export, replacing the plain text header. Relative paths resolve against the project root. |

### `pricing`

A map of model name to `{input, output}` USD-per-1M-tokens, used by `pricing.py` to estimate
the cost written to each report's `*_cost.json`. Entries here are merged over (and override)
the built-in `DEFAULT_PRICES` table - extend it when you add a model that isn't already priced,
or override a price that has changed. A dated model snapshot (e.g. `gpt-4o-2024-08-06`) falls
back to its base name's price if the dated name isn't listed. A model with no matching entry at
all reports $0 cost and logs one warning (per model, per process) rather than failing the
report.

```yaml
pricing:
  gpt-5: { input: 1.25, output: 10.0 }
  gpt-5.4: { input: 2.50, output: 15.0 }
  gpt-4o-mini: { input: 0.15, output: 0.60 }
  text-embedding-3-small: { input: 0.02, output: 0.0 }
```

## Verifying configuration reaches the code

Every key above is read in exactly one place and threaded through explicitly (no hidden global
state): `AppConfig`/`Settings` are constructed once (`get_config()`/`get_settings()`), passed
into `ResearchRunner`, and from there into `DocumentCollector` and `ReportPipeline` as
constructor arguments - so a value changed in `config/settings.yaml` or `.env` takes effect on
the next process start, consistently across the UI and CLI.
