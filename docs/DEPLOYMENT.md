# Mimir Deployment Guide

## Current Live Runtime

Current live Atlas deployment uses the Postgres profile runtime:
- `api-pg` is the container bound to port `8787`
- the older `mimir-api-1` local/SQLite container is stopped

This matters for debugging: if you are checking `http://localhost:8787`, you are talking to `api-pg`, not the old local `api` service.
Confirm before debugging startup, auth, MCP, or retrieval issues:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
docker compose --profile prod-postgres ps
```

## Prerequisites

- Python 3.11+
- Node.js 20+ (web UI only)
- Docker + Docker Compose v2 (container deployment)
- 512 MB RAM minimum; 2 GB recommended for production

---

## Fresh Install (bare metal / VM)

```bash
# 1. Clone and enter the repo
git clone <repo-url> mimir && cd mimir

# 2. Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Create .env from template
cp .env.example .env
# Edit .env — set MIMIR_SECRET_KEY, MIMIR_ENV, etc.

# 4. Run database migrations
alembic upgrade head

# 5. (Optional) Build the web UI
cd web && npm ci && npm run build && cd ..

# 6. Start services
make api       # API on :8787
make worker    # Background worker (separate terminal)
```

---

## Docker Compose — SQLite (local/dev)

Default stack — no external database required.

```bash
# Copy and edit environment file
cp .env.example .env

# Build and start all services
docker compose up --build -d

# Verify API health
curl http://localhost:8787/health

# Check readiness (requires API key)
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/readiness
```

Services: `api` (port 8787) + `worker` + `web` (port 5173).  
Data persists in Docker volume `mimir_data`.

---

## Docker Compose — Postgres (production)

Multi-instance ready. Runs migrations automatically on start.

```bash
# Build and start the Postgres production stack
docker compose --profile prod-postgres up --build -d

# Verify
curl http://localhost:8787/health
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/readiness
```

Services: `postgres` + `api-pg` (port 8787) + `worker-pg` + `web-pg` (port 5173).  
Data persists in volumes `mimir_data` (files) and `postgres_data` (DB).

Note: only one service should own host port `8787` at a time. In the current live Atlas deployment that owner is `api-pg`.

### Connecting Postgres externally

Override the built-in Postgres by setting `MIMIR_DATABASE_URL` in `.env`:

```env
MIMIR_DATABASE_URL=postgresql+asyncpg://user:pass@your-host:5432/mimir
MIMIR_DB_POOL_SIZE=10
MIMIR_DB_MAX_OVERFLOW=20
MIMIR_DB_POOL_TIMEOUT=30
```

Then run only the app services (not the bundled `postgres`):

```bash
docker compose --profile prod-postgres up --build -d api-pg worker-pg web-pg
```

---

## Docker Smoke Test

Validate the stack end-to-end:

```bash
# SQLite smoke test
bash scripts/docker_smoke_test.sh

# Postgres smoke test
PROFILE=prod-postgres bash scripts/docker_smoke_test.sh
```

The script: brings up the stack → checks health/readiness → creates/recalls a memory → checks worker → restarts and verifies persistence → tears down.

---

## Postgres Migration (SQLite → Postgres)

See [UPGRADE.md](UPGRADE.md) for the SQLite-to-Postgres data migration procedure.

---

## Scaling (Postgres)

With `MIMIR_DATABASE_URL` pointing to Postgres you can run multiple API instances behind a load balancer. Workers are automatically prevented from running the same job concurrently via DB-backed job locking (`job_locks` table). Consolidation, lifecycle, graph build, and reflection passes are all protected.

```bash
# Scale API to 3 instances
docker compose --profile prod-postgres up --scale api-pg=3
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MIMIR_ENV` | yes | `development` | `development` or `production` |
| `MIMIR_SECRET_KEY` | prod | `change-me` | Secret for token signing — must be changed |
| `MIMIR_AUTH_MODE` | prod | `""` | `prod` or `dev` |
| `MIMIR_API_KEY` | prod | `local-dev-key` | Legacy single API key |
| `MIMIR_DATA_DIR` | yes | `./data` | Persistent data directory |
| `MIMIR_VECTOR_DIR` | yes | `./data/vectors` | ChromaDB vector store path |
| `MIMIR_DATABASE_URL` | no | `""` | Postgres URL; empty = SQLite in DATA_DIR |
| `MIMIR_DB_POOL_SIZE` | no | `5` | Postgres connection pool size |
| `MIMIR_DB_MAX_OVERFLOW` | no | `10` | Max overflow connections above pool size |
| `MIMIR_DB_POOL_TIMEOUT` | no | `30` | Seconds to wait for a connection |
| `MIMIR_PORT` | no | `8787` | API listen port |
| `MIMIR_CORS_ORIGINS` | prod | `["http://localhost:5173"]` | Allowed CORS origins |
| `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` | no | `false` | Enable `POST /system/consolidate\|reflect\|lifecycle`; default off in prod |
| `MIMIR_SEARCH_BACKEND` | no | auto | `fts5`, `postgres`, or `like` (overrides auto-detect) |
| `SLACK_BOT_TOKEN` | optional | `""` | Slack bot token (enables Slack approvals) |
| `SLACK_SIGNING_SECRET` | if Slack | `""` | Required when Slack is enabled |
| `VAPID_PRIVATE_KEY` | optional | `""` | PWA push — must set both VAPID keys |
| `VAPID_PUBLIC_KEY` | if VAPID | `""` | PWA push public key |

---

## Search Backend Differences

| Backend | Used when | Notes |
|---------|-----------|-------|
| `SQLiteFTSBackend` | SQLite (default) | FTS5 BM25 ranking, isolation columns |
| `PostgresSearchBackend` | Postgres (auto) | `tsvector`/`plainto_tsquery`, on-the-fly |
| `LikeFallbackBackend` | Override (`MIMIR_SEARCH_BACKEND=like`) | Portable, no ranking |

---

## Production Checklist

- [ ] `MIMIR_ENV=production`
- [ ] `MIMIR_SECRET_KEY` changed from default
- [ ] `MIMIR_AUTH_MODE=prod`
- [ ] `MIMIR_API_KEY` rotated from default
- [ ] `MIMIR_CORS_ORIGINS` set to your domain(s)
- [ ] `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` left at `false` (default) unless operator access is needed
- [ ] Security scan passes: `make security`
- [ ] HTTPS termination (nginx/Caddy) in front of port 8787
- [ ] Persistent volumes backed up (see [BACKUP_RESTORE.md](BACKUP_RESTORE.md))
- [ ] Alerts on `/health` endpoint (load balancer probes `/api/system/readiness`)
- [ ] Version confirmed: `GET /health` returns `version: 0.1.0-rc1`
- [ ] Slack signing secret set (if using Slack)
- [ ] Postgres connection string and pool sizing verified
- [ ] Docker smoke test passed: `bash scripts/docker_smoke_test.sh`

---

## Tailscale Notes

Never modify Tailscale configuration from within Mimir.  
The API binds to `0.0.0.0` by default — restrict to a Tailscale interface with `MIMIR_HOST=<tailscale-ip>` if needed.
