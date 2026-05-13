"""P10 adaptive retrieval: provider_stats table + new retrieval_sessions columns + FTS5

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _index_exists(inspector, table: str, index_name: str) -> bool:
    return any(i["name"] == index_name for i in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── New columns on retrieval_sessions ─────────────────────────────────────
    if _table_exists(inspector, "retrieval_sessions"):
        existing = {c["name"] for c in inspector.get_columns("retrieval_sessions")}
        for col_name, col_def in [
            ("task_category", "VARCHAR(32)"),
            ("active_providers", "JSON"),
            ("provider_contributions", "JSON"),
            ("retrieval_confidence_score", "FLOAT"),
        ]:
            if col_name not in existing:
                op.add_column("retrieval_sessions", sa.Column(
                    col_name,
                    sa.String(32) if col_name == "task_category"
                    else sa.JSON() if col_name in ("active_providers", "provider_contributions")
                    else sa.Float(),
                    nullable=True,
                ))

        if not _index_exists(inspector, "retrieval_sessions", "ix_retrieval_sessions_category"):
            op.create_index(
                "ix_retrieval_sessions_category", "retrieval_sessions", ["task_category"]
            )

    # ── provider_stats table ──────────────────────────────────────────────────
    if not _table_exists(inspector, "provider_stats"):
        op.create_table(
            "provider_stats",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("provider_name", sa.String(32), nullable=False),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("task_category", sa.String(32), nullable=True),
            sa.Column("total_sessions", sa.Integer(), server_default="0"),
            sa.Column("useful_sessions", sa.Integer(), server_default="0"),
            sa.Column("harmful_sessions", sa.Integer(), server_default="0"),
            sa.Column("total_memories_contributed", sa.Integer(), server_default="0"),
            sa.Column("usefulness_rate", sa.Float(), server_default="0.5"),
            sa.Column("harmful_rate", sa.Float(), server_default="0.0"),
            sa.Column("avg_agreement_contribution", sa.Float(), server_default="0.5"),
            sa.Column("avg_token_efficiency", sa.Float(), server_default="0.5"),
            sa.Column("weight_current", sa.Float(), server_default="1.0"),
            sa.Column("drift_flagged", sa.Boolean(), server_default="0"),
            sa.Column("drift_reason", sa.String(256), nullable=True),
            sa.Column("drift_detected_at", sa.DateTime(), nullable=True),
            sa.Column("last_updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_provider_stats_name", "provider_stats", ["provider_name"])
        op.create_index("ix_provider_stats_project", "provider_stats", ["project"])
        op.create_index("ix_provider_stats_category", "provider_stats", ["task_category"])
        op.create_index("ix_provider_stats_name_project_cat", "provider_stats",
                        ["provider_name", "project", "task_category"])

    # ── SQLite FTS5 virtual table for keyword search ───────────────────────────
    # Postgres doesn't support VIRTUAL TABLE USING fts5 — skip entirely.
    if bind.dialect.name == "sqlite":
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


def downgrade() -> None:
    bind = op.get_bind()

    # Drop FTS5 triggers and table — SQLite only
    if bind.dialect.name == "sqlite":
        for trigger in ("memories_asoftdel_fts", "memories_au_fts", "memories_ai_fts"):
            try:
                bind.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
            except Exception:
                pass
        try:
            bind.execute(sa.text("DROP TABLE IF EXISTS memory_fts"))
        except Exception:
            pass

    # Drop provider_stats
    for idx in ("ix_provider_stats_name_project_cat", "ix_provider_stats_category",
                "ix_provider_stats_project", "ix_provider_stats_name"):
        try:
            op.drop_index(idx, "provider_stats")
        except Exception:
            pass
    try:
        op.drop_table("provider_stats")
    except Exception:
        pass

    # Remove new columns from retrieval_sessions (SQLite doesn't support DROP COLUMN easily)
    # Leave them in place on downgrade to avoid data loss in SQLite
