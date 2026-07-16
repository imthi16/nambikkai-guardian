SHELL := /bin/sh
.DEFAULT_GOAL := help

PYTHON ?= python3
API_DIR := apps/api
WEB_DIR := apps/web
API_VENV := $(API_DIR)/.venv
API_BIN := $(API_VENV)/bin

.PHONY: help install install-api install-web hooks dev-api dev-web format format-check lint typecheck test build audit check infra-up infra-down infra-logs compose-config compose-build clean

help: ## Show available development commands.
	@awk 'BEGIN {FS = ":.*## "; printf "NambikkAI Guardian commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: install-api install-web ## Install all local development dependencies.

install-api: ## Create the API virtual environment and install development dependencies.
	$(PYTHON) -m venv $(API_VENV)
	$(API_BIN)/python -m pip install --upgrade pip
	$(API_BIN)/python -m pip install -e "$(API_DIR)[dev]"

install-web: ## Install exact frontend dependencies from the lockfile.
	npm --prefix $(WEB_DIR) ci

hooks: ## Install the repository pre-commit hooks.
	$(API_BIN)/pre-commit install

dev-api: ## Run the FastAPI development server on port 8000.
	$(API_BIN)/uvicorn app.main:app --app-dir $(API_DIR) --reload --host 127.0.0.1 --port 8000

dev-web: ## Run the Next.js development server on port 3000.
	npm --prefix $(WEB_DIR) run dev

format: ## Format Python and TypeScript sources.
	$(API_BIN)/ruff format $(API_DIR)/app $(API_DIR)/tests
	$(API_BIN)/ruff check --fix $(API_DIR)/app $(API_DIR)/tests
	npm --prefix $(WEB_DIR) run format

format-check: ## Verify source formatting without changing files.
	$(API_BIN)/ruff format --check $(API_DIR)/app $(API_DIR)/tests
	npm --prefix $(WEB_DIR) run format:check

lint: ## Run backend and frontend linters.
	$(API_BIN)/ruff check $(API_DIR)/app $(API_DIR)/tests
	npm --prefix $(WEB_DIR) run lint

typecheck: ## Run strict Python and TypeScript type checks.
	cd $(API_DIR) && .venv/bin/mypy
	npm --prefix $(WEB_DIR) run typecheck

test: ## Run backend and frontend tests with coverage thresholds.
	cd $(API_DIR) && .venv/bin/pytest
	npm --prefix $(WEB_DIR) run test:coverage

build: ## Create the production frontend build.
	npm --prefix $(WEB_DIR) run build

audit: ## Audit installed Python and locked npm dependencies.
	$(API_BIN)/pip-audit --skip-editable
	npm --prefix $(WEB_DIR) audit --audit-level=high

check: format-check lint typecheck test build compose-config ## Run the primary local quality suite.

infra-up: ## Start PostgreSQL, Redis, MinIO, and bucket initialization.
	docker compose up -d --wait postgres redis minio
	docker compose run --rm --no-deps minio-create-bucket

infra-down: ## Stop local infrastructure without deleting persistent data.
	docker compose down

infra-logs: ## Follow local infrastructure logs.
	docker compose logs -f postgres redis minio minio-create-bucket

compose-config: ## Validate the resolved Docker Compose configuration.
	docker compose config --quiet

compose-build: ## Build production API and web container images.
	docker compose --profile application build api web

clean: ## Remove generated local build and coverage output.
	rm -rf $(API_DIR)/.coverage $(API_DIR)/htmlcov $(WEB_DIR)/.next $(WEB_DIR)/coverage
