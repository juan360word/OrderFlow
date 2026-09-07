# Developer entry points. `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install up up-all down logs migrate revision seed run worker relay queues test test-concurrency test-containers lint format typecheck check reset-db

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Sync the virtualenv from uv.lock
	uv sync --all-extras

up:  ## Start the backing services only (db, cache, queue)
	docker compose up -d db cache queue

up-all:  ## Build and start the whole system (api, relay, worker included)
	docker compose up -d --build

worker:  ## Run the queue consumer locally
	uv run python -m orderflow.worker.consumer

relay:  ## Run the outbox relay locally
	uv run python -m orderflow.worker.relay

queues:  ## Create the SQS queues in LocalStack
	uv run python -m orderflow.worker.bootstrap

down:  ## Stop the containers (data is kept)
	docker compose down

logs:  ## Tail the database logs
	docker compose logs -f db

migrate:  ## Apply every pending migration
	uv run alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add orders"
	uv run alembic revision --autogenerate -m "$(m)"

seed:  ## Create the demo accounts (admin + customer) shown on the login screen
	uv run python -m orderflow.seed

run:  ## Start the API with hot reload
	uv run uvicorn orderflow.main:app --reload --port 8000

test:  ## Run the whole test suite
	uv run pytest

test-concurrency:  ## Run only the concurrency tests
	uv run pytest -m concurrency

test-containers:  ## Run the suite against throwaway containers (testcontainers)
	ORDERFLOW_TEST_BACKEND=testcontainers uv run pytest

# Los mismos tres caminos que revisa CI. `migrations` incluido: un fichero
# generado se lintea igual que uno escrito a mano, y dejarlo fuera aquí es lo
# que permitió que un import mal ordenado pasara `make check` y rompiera CI.
lint:  ## Lint (ruff)
	uv run ruff check src tests migrations

format:  ## Format and auto-fix
	uv run ruff format src tests migrations && uv run ruff check --fix src tests migrations

typecheck:  ## Static type check (mypy --strict)
	uv run mypy

check: lint typecheck test  ## Everything CI runs

reset-db:  ## DESTRUCTIVE: drop the volume and rebuild the schema
	docker compose down -v && docker compose up -d && sleep 6 && $(MAKE) migrate
