# ══════════════════════════════════════════════════════════════════════════════
#  POLYMARKET — by Genoshide | polymarket arbitrage script bot
#  Makefile — developer & user convenience commands
#
#  Usage:  make <target>
#  List:   make help
# ══════════════════════════════════════════════════════════════════════════════

PYTHON     := python
PIP        := pip
VENV       := venv
VENV_BIN   := $(VENV)/bin
# Windows (Git Bash): venv/Scripts; Unix: venv/bin
ifeq ($(OS),Windows_NT)
    VENV_PY  := $(VENV)/Scripts/python
    VENV_PIP := $(VENV)/Scripts/pip
else
    VENV_PY  := $(VENV_BIN)/python
    VENV_PIP := $(VENV_BIN)/pip
endif

.DEFAULT_GOAL := help

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  POLYMARKET ARB BOT — by Genoshide"
	@echo "  ══════════════════════════════════"
	@echo ""
	@echo "  Setup:"
	@echo "    make install      Create venv and install all dependencies"
	@echo "    make setup        Copy .env.example → .env  (first-time setup)"
	@echo "    make health       Run pre-flight checks (connectivity, config)"
	@echo ""
	@echo "  Run:"
	@echo "    make paper        Start bot in paper trading mode (safe)"
	@echo "    make live         Start bot in LIVE mode (real funds)"
	@echo "    make run          Start bot using PAPER_MODE from .env"
	@echo ""
	@echo "  Development:"
	@echo "    make test         Run test suite"
	@echo "    make test-v       Run tests with verbose output"
	@echo "    make lint         Lint code with ruff"
	@echo "    make format       Auto-format code with ruff"
	@echo "    make typecheck    Run mypy type checker"
	@echo "    make check        Run lint + typecheck + tests"
	@echo ""
	@echo "  Maintenance:"
	@echo "    make logs         Tail the live log file"
	@echo "    make clean-logs   Delete all .log files"
	@echo "    make clean        Remove __pycache__ and .pyc files"
	@echo "    make clean-all    Remove venv, caches, and logs"
	@echo "    make deps-update  Upgrade all pip packages"
	@echo ""
	@echo "  Docker:"
	@echo "    make docker-build  Build the Docker image"
	@echo "    make docker-paper  Run paper mode in Docker (interactive)"
	@echo "    make docker-live   Run live mode in Docker (5s abort window)"
	@echo "    make docker-up     Start bot as background daemon"
	@echo "    make docker-stop   Stop the running container"
	@echo "    make docker-logs   Tail container log output"
	@echo "    make docker-health Run health check inside container"
	@echo "    make docker-shell  Open a shell inside the container"
	@echo "    make docker-clean  Remove image, volumes, containers"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: install
install:
	@echo "→ Creating virtual environment..."
	$(PYTHON) -m venv $(VENV)
	@echo "→ Upgrading pip..."
	$(VENV_PIP) install --upgrade pip setuptools wheel
	@echo "→ Installing dependencies..."
	$(VENV_PIP) install -r requirements.txt
	@echo ""
	@echo "  ✓ Installation complete."
	@echo "  Next: make setup  (to configure .env)"

.PHONY: setup
setup:
	@if [ -f .env ]; then \
		echo "  .env already exists — skipping. Edit it manually if needed."; \
	else \
		cp .env.example .env; \
		echo "  ✓ .env created from .env.example"; \
		echo "  → Open .env and fill in your credentials before running."; \
	fi

.PHONY: health
health:
	$(VENV_PY) healthcheck.py

# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: paper
paper:
	$(VENV_PY) main.py --paper

.PHONY: live
live:
	@echo "⚠  Starting in LIVE mode — real funds will be used."
	@echo "   Press Ctrl+C within 5 seconds to abort."
	@sleep 5
	$(VENV_PY) main.py --live

.PHONY: run
run:
	$(VENV_PY) main.py

# ─────────────────────────────────────────────────────────────────────────────
# Development
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: test
test:
	$(VENV_PY) -m pytest tests/ -q

.PHONY: test-v
test-v:
	$(VENV_PY) -m pytest tests/ -v

.PHONY: lint
lint:
	$(VENV_PY) -m ruff check .

.PHONY: format
format:
	$(VENV_PY) -m ruff format .
	$(VENV_PY) -m ruff check --fix .

.PHONY: typecheck
typecheck:
	$(VENV_PY) -m mypy . --ignore-missing-imports

.PHONY: check
check: lint typecheck test
	@echo "✓ All checks passed."

# ─────────────────────────────────────────────────────────────────────────────
# Maintenance
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: logs
logs:
	tail -f polymarket_bot.log

.PHONY: clean-logs
clean-logs:
	@rm -f *.log *.log.*
	@echo "✓ Log files deleted."

.PHONY: clean
clean:
	@find . -type d -name __pycache__ ! -path "./venv/*" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" ! -path "./venv/*" -delete 2>/dev/null || true
	@echo "✓ Caches cleaned."

.PHONY: clean-all
clean-all: clean clean-logs
	@rm -rf $(VENV) .mypy_cache .ruff_cache .pytest_cache
	@echo "✓ Full clean done. Run 'make install' to reinstall."

.PHONY: deps-update
deps-update:
	$(VENV_PIP) install --upgrade -r requirements.txt
	@echo "✓ Dependencies updated."

# ─────────────────────────────────────────────────────────────────────────────
# Docker
# ─────────────────────────────────────────────────────────────────────────────
DOCKER_IMAGE := polymarket-bot

.PHONY: docker-build
docker-build:
	@echo "→ Building Docker image '$(DOCKER_IMAGE)'..."
	docker build -t $(DOCKER_IMAGE):latest .
	@echo "✓ Image built: $(DOCKER_IMAGE):latest"

.PHONY: docker-paper
docker-paper: docker-build
	@echo "→ Starting bot in PAPER mode (interactive, with dashboard)..."
	docker compose run --rm -it bot --paper

.PHONY: docker-live
docker-live: docker-build
	@echo "⚠  Starting in LIVE mode inside Docker — real funds will be used."
	@echo "   Press Ctrl+C within 5 seconds to abort."
	@sleep 5
	docker compose run --rm -it bot --live

.PHONY: docker-up
docker-up: docker-build
	@echo "→ Starting bot in daemon mode (paper mode from .env, logs only)..."
	@mkdir -p logs
	docker compose up -d
	@echo "✓ Container started. Tail logs with: make docker-logs"

.PHONY: docker-stop
docker-stop:
	docker compose down
	@echo "✓ Container stopped."

.PHONY: docker-logs
docker-logs:
	docker compose logs -f

.PHONY: docker-health
docker-health:
	docker compose run --rm bot python healthcheck.py

.PHONY: docker-shell
docker-shell:
	docker compose run --rm -it --entrypoint /bin/bash bot

.PHONY: docker-clean
docker-clean:
	docker compose down --rmi local --volumes --remove-orphans 2>/dev/null || true
	@echo "✓ Docker resources cleaned."
