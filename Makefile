.PHONY: venv install install-dev dev api mcp worker web migrate migrate-legacy test lint evals gate ci release security

# ── Install ───────────────────────────────────────────────────────────────────

venv:
	python3 -m venv .venv

# Managed-environment Linux (Debian/Ubuntu) safe install — always use the venv
install-dev: venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@echo "Done. Activate with: source .venv/bin/activate"

# Legacy bare-pip install (works in unrestricted environments)
install:
	pip install -e ".[dev]"

# ── Runtime ───────────────────────────────────────────────────────────────────

dev: migrate
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8787

api: migrate
	uvicorn api.main:app --host 0.0.0.0 --port 8787

mcp:
	python -m mcp.server

worker:
	python -m worker.scheduler

web:
	cd web && npm run dev

web-install:
	cd web && npm install

# ── Schema ────────────────────────────────────────────────────────────────────

# Canonical schema setup — runs Alembic migrations
migrate:
	alembic upgrade head

# Legacy init path (used by tests via storage.database.init_db directly)
migrate-legacy:
	python -m storage.database --migrate

# ── Quality ──────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v

lint:
	ruff check .

# ── Evals & Release Gate ──────────────────────────────────────────────────────

evals:
	python -m evals.runner --suite all --out reports/evals/latest.json

gate:
	python -m evals.release_gate --run-evals

ci: test evals gate

release:
	python -m evals.release_report --out reports/release/latest

security:
	./scripts/security_scan.sh

# ── First-time setup ─────────────────────────────────────────────────────────

all: install-dev migrate
	@echo "Mimir ready. Run: source .venv/bin/activate && make dev"
