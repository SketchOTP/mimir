# Mimir — Database Migrations

Mimir uses [Alembic](https://alembic.sqlalchemy.org/) for schema evolution.
The database is SQLite; all migrations use `render_as_batch=True` for safe
`ALTER TABLE` support.

## Running Migrations

```bash
make migrate         # equivalent to: alembic upgrade head
```

This is idempotent — safe to run on both fresh and existing databases.

## Fresh Install

```bash
make install-dev
make migrate         # creates all tables via the 0001_initial migration
make dev
```

## Checking Current Version

```bash
alembic current      # shows applied revision
alembic history      # shows all revisions
```

## Creating a New Migration

After modifying `storage/models.py`:

```bash
alembic revision --autogenerate -m "add_column_foo"
```

Review the generated file in `migrations/versions/`, then:

```bash
alembic upgrade head
```

## Rolling Back

```bash
alembic downgrade -1        # one step back
alembic downgrade base      # full rollback (drops all tables)
```

**WARNING:** Downgrading the initial migration (`0001`) drops all tables.
Always back up your data first.

## Migration Table Reference

| Revision | Description |
|----------|-------------|
| `0001` | Initial schema — all 17 Mimir tables |

## Pre-Alembic Databases

If your database was created before Alembic was added (via the old
`make migrate-legacy` / `python -m storage.database --migrate` path),
stamp it at head before running future migrations:

```bash
alembic stamp head    # marks DB as current without running 0001
alembic upgrade head  # picks up any future migrations
```

See [upgrade.md](upgrade.md) for the full upgrade procedure.

## Configuration

The database URL is derived from `MIMIR_DATA_DIR` at runtime.
Override for a one-off migration:

```bash
MIMIR_DATA_DIR=/path/to/data alembic upgrade head
```

The `alembic.ini` also contains a `sqlalchemy.url` fallback for offline
SQL generation (`alembic upgrade head --sql`).
