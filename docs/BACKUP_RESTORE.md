# Backup and Restore

Mimir stores state in two places that must both be backed up:

1. **SQLite database** — `data/mimir.db` (all memories, approvals, graph, simulations, telemetry)
2. **ChromaDB vector store** — `data/vectors/` (embeddings for semantic search)

---

## Create a Backup

```bash
# Default output: data/backups/mimir_backup_YYYYMMDD_HHMMSS.zip
python -m mimir.backup.create

# Custom output directory
python -m mimir.backup.create --out /mnt/backups/mimir/
```

The archive contains:
- `manifest.json` — metadata (version, timestamp, migration revision)
- `mimir.db` — full SQLite database
- `vectors/` — ChromaDB persistent files

---

## Verify a Backup

```bash
python -m mimir.backup.verify /path/to/mimir_backup_20260501_120000.zip
```

Checks:
- Archive is a valid zip
- `manifest.json` present with required fields
- `mimir.db` present, non-empty, valid SQLite magic header
- Migration version in DB matches manifest
- Vector files present

---

## Restore a Backup

**Stop the API and worker before restoring.**

```bash
# Dry run — validates without writing anything
python -m mimir.backup.restore /path/to/mimir_backup.zip --dry-run

# Full restore
python -m mimir.backup.restore /path/to/mimir_backup.zip
```

The restore command:
1. Saves existing `mimir.db` → `mimir.db.pre_restore`
2. Saves existing `vectors/` → `vectors.pre_restore/`
3. Extracts DB and vector files from archive
4. Prints instructions to run `alembic upgrade head`

**After restore:**

```bash
# Apply any pending migrations
alembic upgrade head

# Rebuild FTS index (if needed)
python -m storage.reindex_fts

# Rebuild vector index (if vectors were absent or corrupted)
# Re-embedding is automatic on next startup when vector store is empty
```

---

## Automated Backups

Add to crontab:

```cron
# Daily backup at 2am
0 2 * * * cd /opt/mimir && .venv/bin/python -m mimir.backup.create --out /mnt/backups/mimir/
```

Or use Docker Compose with a backup sidecar:

```yaml
backup:
  image: python:3.11-slim
  volumes:
    - mimir_data:/app/data
    - ./backups:/backups
  command: >
    sh -c "pip install -e /app -q && python -m mimir.backup.create --out /backups/"
  profiles: ["backup"]
```

---

## Smoke Test After Restore

```bash
# 1. Health check
curl http://localhost:8787/health

# 2. Readiness check
curl -H "X-API-Key: $MIMIR_API_KEY" http://localhost:8787/api/system/readiness

# 3. Recall a memory to confirm retrieval works
curl -X POST http://localhost:8787/api/events/recall \
  -H "X-API-Key: $MIMIR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "test recall after restore"}'
```
