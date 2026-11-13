.PHONY: help install uninstall test lint typecheck coverage clean dev lock upgrade

VENV ?= .venv
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate: pyproject.toml uv.lock
	uv sync --extra dev
	touch $(VENV)/bin/activate

dev: $(VENV)/bin/activate ## Create .venv and install package with dev deps (via uv sync)

lock: ## Regenerate uv.lock from pyproject.toml
	uv lock

upgrade: ## Upgrade all locked dependencies to latest compatible versions
	uv lock --upgrade

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

docker/bin/xray docker/bin/sing-box:
	mkdir -p docker/bin
	curl -fsSL -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/download/v1.8.24/Xray-linux-64.zip
	unzip -jo /tmp/xray.zip xray -d docker/bin
	curl -fsSL -o /tmp/sing-box.tar.gz https://github.com/SagerNet/sing-box/releases/download/v1.10.7/sing-box-1.10.7-linux-amd64.tar.gz
	tar -C docker/bin --strip-components=1 -xzf /tmp/sing-box.tar.gz sing-box-1.10.7-linux-amd64/sing-box
	chmod 755 docker/bin/xray docker/bin/sing-box

test-docker: docker/bin/xray docker/bin/sing-box ## Proxied request per protocol inside Docker
	docker build -t omarchy-proxy-test -f docker/Dockerfile .
	docker run --rm -v "$(CURDIR)":/src -w /src -e PYTHONPATH=/src/src omarchy-proxy-test

install: ## Install the plugin backend, systemd user unit, and tun helper
	./install.sh --with-helper

uninstall: ## Remove installed files; keeps user config/state
	./uninstall.sh
