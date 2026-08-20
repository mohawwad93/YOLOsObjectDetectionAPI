.PHONY: sync test lint

sync:
	uv sync --locked --extra cpu

test:
	uv run pytest

lint:
	uv run ruff check . --fix && uv run ruff format .