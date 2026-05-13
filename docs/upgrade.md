# Mimir — Upgrade Guide

## Standard Upgrade (new code, same schema)

```bash
git pull
make install-dev      # picks up any new Python dependencies
make migrate          # runs any new Alembic migrations
```

Restart services after upgrading:

```bash
# If running manually:
pkill -f "uvicorn api.main"   # stop API
pkill -f "worker.scheduler"   # stop worker
make dev                      # restart API
make worker                   # restart worker
```

## Upgrading from pre-Alembic versions

Versions before Phase 4 used `python -m storage.database --migrate` (raw
`create_all`).  These databases have no `alembic_version` table.

### Step-by-step safe upgrade

1. **Back up your data:**

   ```bash
   cp -r data/ data.bak/
   ```

2. **Stamp the existing database at head** (tells Alembic the schema is
   already current, without modifying any tables):

   ```bash
   alembic stamp head
   ```

3. **Pull new code and install:**

   ```bash
   git pull
   make install-dev
   ```

4. **Run migrations** (picks up any new revisions after 0001):

   ```bash
   alembic upgrade head
   ```

5. **Run the backfill** (optional — fixes `promoted_at` for old promotions so
   the rollback watcher can process them):

   ```bash
   python - <<'EOF'
   import asyncio
   from storage.database import init_db, get_session_factory
   from approvals.promotion_worker import backfill_promoted_at

   async def main():
       await init_db()
       async with get_session_factory()() as session:
           n = await backfill_promoted_at(session)
           print(f"Backfilled {n} promoted improvements")

   asyncio.run(main())
   EOF
   ```

6. **Restart services.**

## Rolling Back a Migration

```bash
alembic downgrade -1    # one step back
```

Then restore your data backup if the downgrade was destructive.

## Verifying After Upgrade

```bash
alembic current           # should show 0001 (or latest)
make test                 # all tests should pass
curl http://localhost:8787/api/health  # {"status": "ok"}
```

## Common Issues

### `externally-managed-environment` on Ubuntu/Debian

```bash
make install-dev   # always uses .venv — never the system pip
```

### `alembic: command not found`

Activate the venv first:

```bash
source .venv/bin/activate
alembic upgrade head
```

Or run via the venv directly:

```bash
.venv/bin/alembic upgrade head
```

### `Target database is not up to date`

Run `alembic upgrade head` to apply pending migrations.

### Schema drift after manual edits

If you modified the DB directly (e.g. via sqlite3), re-stamp and then
reconcile manually or drop + recreate:

```bash
alembic stamp head   # if you're sure schema matches head
```
