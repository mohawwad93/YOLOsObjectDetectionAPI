.PHONY: sync test lint lint-fix

sync:
	uv sync --locked --group dev --extra cpu

test:
	uv run --extra cpu --group dev pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

lint-fix:
	uv run ruff check --fix .
	uv run ruff format .