# Contributing

Thanks for considering a contribution to SEC Research Agent.

## Getting set up

```bash
git clone https://github.com/hammuneer/sec-research-agent.git
cd sec-research-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # add your own API keys for manual testing
```

See the [README](README.md) for the full configuration reference and how to run the app.

## Workflow

1. Open an issue first for anything non-trivial (new features, behavior changes) so we can
   discuss the approach before you invest time.
2. Create a branch off `main`.
3. Make your change, with tests where it makes sense.
4. Run the checks locally before opening a PR:

   ```bash
   make lint       # ruff check
   make format     # ruff format
   make typecheck  # mypy
   make test       # pytest
   ```

   (or the equivalent `ruff` / `mypy` / `pytest` commands directly — see the Makefile).
5. Open a pull request describing **what** changed and **why**. Link the issue it addresses.

## Code style

- Python 3.11+, type-hinted, formatted and linted with [ruff](https://docs.astral.sh/ruff/)
  (`pyproject.toml` has the exact rule set).
- Keep modules focused: collection (`sources/`, `pipeline/collect.py`), report generation
  (`rag/`, `reports/`, `pipeline/report.py`), and the UI (`ui/`) are intentionally separate
  layers — avoid reaching across them directly.
- Docstrings on public functions/classes; prefer clear names over comments.

## Reporting bugs / requesting features

Open a GitHub issue with:

- What you expected vs. what happened
- Steps to reproduce (ticker symbols, config, etc. — never paste API keys)
- Your Python version and OS

## Security issues

Do not open a public issue for a security vulnerability — see [SECURITY.md](SECURITY.md).

## Code of conduct

Be respectful and constructive. We want this to be a welcoming project for anyone
interested in SEC filings, RAG pipelines, or equity research tooling.
