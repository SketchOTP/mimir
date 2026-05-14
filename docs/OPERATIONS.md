# Operations Guide

## Current Live Runtime

Current live Atlas deployment uses the Postgres-backed `api-pg` container on port `8787`.
The older SQLite/local `mimir-api-1` container is intentionally stopped in this runtime.
If `localhost:8787` behaves differently than expected, first confirm which container owns the port:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
docker compose --profile prod-postgres ps
```

Expected current state:
- `api-pg` bound to `0.0.0.0:8787->8787/tcp`
- old `mimir-api-1` stopped
- live Atlas/MCP traffic terminating at `api-pg`

## Health and Readiness

```bash
# Basic liveness (no auth)
curl http://localhost:8787/health
# → {"status": "ok", "service": "mimir"}

# Full readiness (requires auth)
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/readiness
# → {"ready": true, "checks": {"database": {"ok": true}, "migration": {...}, ...}}

# Component status
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/status

# Background jobs
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/jobs
```

---

## Reindex Vectors

If the vector store is corrupted or empty after a restore:

```bash
# Vectors are rebuilt automatically on the next embedding request.
# To force a rebuild, delete the vector directory and restart:
rm -rf data/vectors/
make dev    # ChromaDB recreates on first write
```

---

## Reindex FTS

If the keyword search index is out of sync (SQLite only):

```bash
python -m storage.reindex_fts
```

Clears and rebuilds the `memory_fts` virtual table from the `memories` table.
On Postgres, keyword search uses `tsvector` computed on-the-fly — no reindex needed.

---

## Multi-Worker Job Locking

When running multiple worker instances (Postgres deployment), jobs are protected by
DB-backed locks in the `job_locks` table. Protected jobs: `consolidation_pass`,
`lifecycle_pass`, `graph_build`, `reflection_pass`.

```bash
# Check active job locks
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/jobs

# Stale locks (expired heartbeat) are automatically purged by the next worker
# that tries to acquire them. Manual cleanup if needed:
python -c "
import asyncio
from storage.database import get_session_factory
from worker.job_lock import _purge_stale
async def main():
    async with get_session_factory()() as s:
        n = await _purge_stale(s)
        await s.commit()
        print(f'Purged {n} stale locks')
asyncio.run(main())
"
```

---

## Trigger Worker Jobs Manually

```bash
# Consolidation pass (trust updates, episodic chains, dedup)
curl -X POST http://localhost:8787/api/system/consolidate \
  -H "X-API-Key: $MIMIR_API_KEY"

# Reflection pass (pattern analysis, improvement proposals)
curl -X POST http://localhost:8787/api/system/reflect \
  -H "X-API-Key: $MIMIR_API_KEY"

# Lifecycle pass (aging, archiving, verification decay)
curl -X POST http://localhost:8787/api/system/lifecycle \
  -H "X-API-Key: $MIMIR_API_KEY"

# Graph build pass
curl -X POST http://localhost:8787/api/graph/build \
  -H "X-API-Key: $MIMIR_API_KEY"
```

---

## Worker Schedule (default)

| Task | Interval |
|------|----------|
| Reflector | Every 30 min |
| Consolidator | Nightly (24h) |
| Lifecycle | Nightly (24h) |
| Deep maintenance | Weekly |
| Telemetry snapshot | Every 6h |
| Drift detection | Daily |
| Provider stats aggregation | Every 6h |
| Graph build | Every 24h |
| Forecast calibration | Daily |

---

## Debug a Failed Worker Job

1. Check running jobs: `GET /api/system/jobs`
2. Trigger manually and watch logs: `POST /api/system/consolidate`
3. Check worker process logs — APScheduler logs to `INFO` by default
4. Set `MIMIR_ENV=development` to get verbose logging

---

## Debug Recall Leakage

If users report seeing each other's memories:

1. Check release gate: `python -m evals.release_gate`
2. Run retrieval quality eval: `python -m evals.runner --suite retrieval_quality`
3. Check FTS isolation: the `fts_cross_user_leakage_rate` metric must be 0
4. Reindex FTS if schema is pre-0011: `python -m storage.reindex_fts`
5. Check keyword provider: must pass `user_id` to `fts5_search`

---

## Monitor Key Metrics

```bash
# Get all current metrics
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/metrics

# Provider effectiveness
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/providers/stats

# Drift alerts
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/providers/drift

# Telemetry snapshot
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/telemetry/snapshot
```

---

## Pending Approvals

Approvals that sit in `pending` state for more than the expiry window are auto-expired by the lifecycle pass.

```bash
# List pending approvals
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/approvals?status=pending
```

---

## Log Format

Mimir emits structured JSON logs via `mimir.logging`:

```json
{"timestamp": "2026-05-13T12:00:00Z", "event": "startup", "component": "api", "status": "ok"}
```

Set `MIMIR_ENV=development` for human-readable output.

---

## Known Limitations (0.1.0-rc1)

| Area | Limitation | Workaround |
|------|-----------|------------|
| **SQLite** | SQLite is intended for dev/local use only. Concurrent writes lock the DB. | Use Postgres for production multi-user deployments. |
| **Docker image size** | `sentence-transformers` pulls torch+CUDA → ~9GB image. | Pin torch to CPU-only: `pip install torch --index-url https://download.pytorch.org/whl/cpu` before installing mimir. |
| **Docker disk** | `docker compose up --build` for prod-postgres profile requires ~40GB free disk. | Build images separately; use pre-built image with `image:` instead of `build:` in compose. |
| **Graph UI** | No visual graph explorer in the web UI — graph data accessible via API only. | Use `GET /api/graph/traverse/{entity_id}` for programmatic access. |
| **Visual DAG** | Simulation plan step dependencies shown as table, not directed graph. | Low priority; `react-flow` integration tracked as future work. |
| **Historical simulation matching** | `get_simulation_context` uses keyword search, not vector similarity. | High-quality simulation retrieval will improve as vector search is wired in. |
| **Postgres load** | Default pool_size=5 is too small for >10 concurrent users. | Set `MIMIR_DB_POOL_SIZE=20` or use PgBouncer in front of Postgres. |
| **Observer not wired** | `worker/observer.py` is a library but not called by API routes. | Track/trace events must be sent explicitly by the calling system. |
| **System mutation endpoints** | `POST /system/consolidate|reflect|lifecycle` disabled by default; workers run on schedule. | Enable with `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true` for manual operator triggers. |
