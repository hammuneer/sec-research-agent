.PHONY: install dev run test lint format typecheck docker-build docker-run clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

run:
	streamlit run app.py

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy src

docker-build:
	docker build -t sec-research-agent .

docker-run:
	docker run --rm -p 8501:8501 --env-file .env -v "$(PWD)/data:/data" sec-research-agent

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info htmlcov .coverage
