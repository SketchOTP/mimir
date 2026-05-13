"""P15 FTS5 isolation: add user_id + project_id to memory_fts table.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-13

Drops and recreates the memory_fts virtual table with two new UNINDEXED
columns (user_id, project_id) so that FTS queries can be pre-filtered at
the FTS level rather than relying solely on a post-filter SQL join.

NULL user_id / project are stored as '' so standard equality comparisons
work correctly inside FTS5 virtual table WHERE clauses.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Postgres doesn't support VIRTUAL TABLE USING fts5 — skip entirely.
    if bind.dialect.name != "sqlite":
        return

    # ── Drop old FTS5 triggers ────────────────────────────────────────────────
    for trigger in ("memories_asoftdel_fts", "memories_au_fts", "memories_ai_fts"):
        try:
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
        except Exception:
            pass

    # ── Drop old FTS5 table ───────────────────────────────────────────────────
    try:
        bind.execute(sa.text("DROP TABLE IF EXISTS memory_fts"))
    except Exception:
        pass

    # ── Recreate FTS5 table with user_id + project_id ─────────────────────────
    try:
        bind.execute(sa.text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5("
            "  memory_id UNINDEXED, "
            "  user_id UNINDEXED, "
            "  project_id UNINDEXED, "
            "  content, "
            "  tokenize='unicode61 remove_diacritics 1'"
            ")"
        ))

        # Backfill from existing active memories (NULL → '' for isolation columns)
        bind.execute(sa.text(
            "INSERT OR IGNORE INTO memory_fts(memory_id, user_id, project_id, content) "
            "SELECT id, COALESCE(user_id, ''), COALESCE(project, ''), content "
            "FROM memories WHERE deleted_at IS NULL AND memory_state != 'quarantined'"
        ))

        # ── AFTER INSERT trigger ───────────────────────────────────────────────
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_ai_fts
AFTER INSERT ON memories
WHEN NEW.deleted_at IS NULL AND NEW.memory_state != 'quarantined'
BEGIN
    INSERT INTO memory_fts(memory_id, user_id, project_id, content)
    VALUES (NEW.id, COALESCE(NEW.user_id, ''), COALESCE(NEW.project, ''), NEW.content);
END
        """))

        # ── AFTER UPDATE (content changed) trigger ────────────────────────────
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_au_fts
AFTER UPDATE OF content, user_id, project ON memories
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
    INSERT INTO memory_fts(memory_id, user_id, project_id, content)
    SELECT NEW.id, COALESCE(NEW.user_id, ''), COALESCE(NEW.project, ''), NEW.content
    WHERE NEW.deleted_at IS NULL AND NEW.memory_state != 'quarantined';
END
        """))

        # ── AFTER soft-delete trigger ─────────────────────────────────────────
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_asoftdel_fts
AFTER UPDATE OF deleted_at ON memories
WHEN NEW.deleted_at IS NOT NULL
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
END
        """))

        # ── AFTER quarantine trigger ──────────────────────────────────────────
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_aquar_fts
AFTER UPDATE OF memory_state ON memories
WHEN NEW.memory_state = 'quarantined'
BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
END
        """))

    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name != "sqlite":
        return

    for trigger in ("memories_aquar_fts", "memories_asoftdel_fts",
                    "memories_au_fts", "memories_ai_fts"):
        try:
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
        except Exception:
            pass

    try:
        bind.execute(sa.text("DROP TABLE IF EXISTS memory_fts"))
    except Exception:
        pass

    # Restore 0008-era FTS5 table (two-column schema)
    try:
        bind.execute(sa.text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(memory_id UNINDEXED, content, tokenize='unicode61 remove_diacritics 1')"
        ))
        bind.execute(sa.text(
            "INSERT OR IGNORE INTO memory_fts(memory_id, content) "
            "SELECT id, content FROM memories WHERE deleted_at IS NULL"
        ))
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_ai_fts
AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(memory_id, content) VALUES (NEW.id, NEW.content);
END
        """))
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_au_fts
AFTER UPDATE OF content ON memories BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
    INSERT INTO memory_fts(memory_id, content) VALUES (NEW.id, NEW.content);
END
        """))
        bind.execute(sa.text("""
CREATE TRIGGER IF NOT EXISTS memories_asoftdel_fts
AFTER UPDATE OF deleted_at ON memories WHEN NEW.deleted_at IS NOT NULL BEGIN
    DELETE FROM memory_fts WHERE memory_id = OLD.id;
END
        """))
    except Exception:
        pass
