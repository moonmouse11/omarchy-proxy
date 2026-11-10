.PHONY: help install uninstall test lint typecheck coverage clean dev

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate: pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e '.[dev]'
	touch $(VENV)/bin/activate

dev: $(VENV)/bin/activate ## Create .venv and install package with dev deps

test: dev ## Run unit tests
	$(PYTEST)

coverage: dev ## Run tests with coverage
	$(PYTEST) --cov --cov-report=term-missing

lint: dev ## Run ruff
	$(RUFF) check src tests
	$(RUFF) format --check src tests

format: dev ## Auto-format
	$(RUFF) check --fix src tests
	$(RUFF) format src tests

typecheck: dev ## Run mypy
	$(MYPY)

clean: ## Remove caches and build artifacts
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

install: ## Install the plugin backend and systemd user unit
	./install.sh

uninstall: ## Remove installed files; keeps user config/state
	./uninstall.sh
