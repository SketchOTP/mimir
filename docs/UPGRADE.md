# Upgrade Guide

## Standard Upgrade

```bash
# 1. Back up data before upgrading
python -m mimir.backup.create --out backups/pre-upgrade/

# 2. Pull latest code
git pull

# 3. Install updated dependencies
pip install -e ".[dev]"

# 4. Apply database migrations
alembic upgrade head

# 5. Build web UI (if changed)
cd web && npm ci && npm run build && cd ..

# 6. Restart services
# (systemd / Docker / process manager specific — see below)
```

---

## Docker Compose Upgrade

```bash
# 1. Back up volume
docker compose exec api python -m mimir.backup.create --out /app/data/backups/

# 2. Pull and rebuild
git pull
docker compose build

# 3. Restart with migrations
docker compose down && docker compose up -d
# Migrations run automatically on api startup (alembic upgrade head in CMD)
```

---

## Migration Rollback

If a migration causes problems:

```bash
# Check current revision
alembic current

# Roll back one step
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade 0010
```

All migrations in `migrations/versions/` are reversible (they include `downgrade()` functions).

---

## Checking Migration Status

```bash
# Current revision
alembic current

# Full revision history
alembic history --verbose

# Pending migrations
alembic heads
```

---

## Rolling Back to a Backup After a Failed Upgrade

```bash
# 1. Stop services
docker compose down  # or kill processes

# 2. Restore the pre-upgrade backup
python -m mimir.backup.restore backups/pre-upgrade/mimir_backup_*.zip

# 3. Downgrade code
git checkout <previous-tag>

# 4. Reinstall dependencies
pip install -e ".[dev]"

# 5. Restart
make dev
```

---

## SQLite → Postgres Migration

To migrate a production SQLite deployment to Postgres:

```bash
# 1. Back up SQLite data
python -m mimir.backup.create --out backups/sqlite-final/

# 2. Set up Postgres and export the SQLite DB
#    Use pgloader or a manual SQL export tool, e.g.:
pip install pgloader  # or use Docker: docker run dimitri/pgloader
pgloader sqlite:///./data/mimir.db postgresql://mimir:pass@host:5432/mimir

# 3. Run Alembic migrations on Postgres (to stamp the revision)
MIMIR_DATABASE_URL=postgresql+asyncpg://... alembic stamp head

# 4. Set MIMIR_DATABASE_URL in .env and restart
#    The FTS keyword search will automatically switch to PostgresSearchBackend

# 5. Reindex search (optional — Postgres FTS is computed on-the-fly)
python -m storage.reindex_fts  # no-op on Postgres; safe to run anyway
```

**Note:** SQLite FTS5 virtual table (`memory_fts`) is not migrated — Postgres uses
`tsvector`/`plainto_tsquery` automatically. No manual setup needed.

---

## Migration Notes

| Migration | What changed | Notes |
|-----------|-------------|-------|
| 0001 | Initial schema | Memories, approvals, rollbacks |
| 0002 | Audit log table | Approval audit trail |
| 0003–0004 | Trust + temporal fields | Memory state machine |
| 0005 | Lifecycle engine | Episodic chains, lifecycle events |
| 0006 | Procedural learning | Evidence count, retrieval feedback |
| 0007 | Telemetry | Retrieval sessions, telemetry snapshots |
| 0008 | Adaptive retrieval | Provider stats, FTS5 virtual table (SQLite only) |
| 0009 | Graph memory | graph_nodes, graph_edges |
| 0010 | Simulation | simulation_plans, simulation_runs, forecast_calibration |
| 0011 | FTS isolation | Adds user_id+project_id to memory_fts; run reindex_fts after |
| 0012 | Job locks | job_locks table for multi-worker distributed locking |
