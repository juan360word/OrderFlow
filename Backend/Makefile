# Developer entry points. `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install up down logs migrate revision run test test-concurrency lint format typecheck check reset-db

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Sync the virtualenv from uv.lock
	uv sync --all-extras

up:  ## Start PostgreSQL and Redis
	docker compose up -d

down:  ## Stop the containers (data is kept)
	docker compose down

logs:  ## Tail the database logs
	docker compose logs -f db

migrate:  ## Apply every pending migration
	uv run alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add orders"
	uv run alembic revision --autogenerate -m "$(m)"

run:  ## Start the API with hot reload
	uv run uvicorn orderflow.main:app --reload --port 8000

test:  ## Run the whole test suite
	uv run pytest

test-concurrency:  ## Run only the concurrency tests
	uv run pytest -m concurrency

lint:  ## Lint (ruff)
	uv run ruff check src tests

format:  ## Format and auto-fix
	uv run ruff format src tests migrations && uv run ruff check --fix src tests

typecheck:  ## Static type check (mypy --strict)
	uv run mypy

check: lint typecheck test  ## Everything CI runs

reset-db:  ## DESTRUCTIVE: drop the volume and rebuild the schema
	docker compose down -v && docker compose up -d && sleep 6 && $(MAKE) migrate
