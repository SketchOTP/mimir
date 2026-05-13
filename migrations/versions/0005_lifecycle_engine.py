"""add lifecycle engine tables and retrieval frequency fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Retrieval frequency fields on memories ────────────────────────────────
    for col_name, col_type, default in [
        ("times_retrieved", sa.Integer(), "0"),
        ("last_retrieved_at", sa.DateTime(), None),
        ("successful_retrievals", sa.Integer(), "0"),
        ("failed_retrievals", sa.Integer(), "0"),
    ]:
        if not _col_exists(inspector, "memories", col_name):
            op.add_column("memories", sa.Column(col_name, col_type, nullable=True))
            if default is not None:
                bind.execute(sa.text(
                    f"UPDATE memories SET {col_name} = {default} WHERE {col_name} IS NULL"
                ))

    # ── episodic_chains table ─────────────────────────────────────────────────
    if not _table_exists(inspector, "episodic_chains"):
        op.create_table(
            "episodic_chains",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("episode_summary", sa.Text(), nullable=True),
            sa.Column("episode_type", sa.String(64), nullable=False, server_default="incident"),
            sa.Column("linked_memory_ids", sa.JSON(), nullable=True),
            sa.Column("procedural_lesson", sa.Text(), nullable=True),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_episodic_chains_project", "episodic_chains", ["project"])

    # ── lifecycle_events table ────────────────────────────────────────────────
    if not _table_exists(inspector, "lifecycle_events"):
        op.create_table(
            "lifecycle_events",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("memory_id", sa.String(64), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("from_state", sa.String(20), nullable=True),
            sa.Column("to_state", sa.String(20), nullable=True),
            sa.Column("trust_before", sa.Float(), nullable=True),
            sa.Column("trust_after", sa.Float(), nullable=True),
            sa.Column("reason", sa.String(256), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_lifecycle_events_memory", "lifecycle_events", ["memory_id"])
        op.create_index("ix_lifecycle_events_type", "lifecycle_events", ["event_type"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for idx in ["ix_lifecycle_events_type", "ix_lifecycle_events_memory"]:
        try:
            op.drop_index(idx, "lifecycle_events")
        except Exception:
            pass
    if _table_exists(inspector, "lifecycle_events"):
        op.drop_table("lifecycle_events")

    for idx in ["ix_episodic_chains_project"]:
        try:
            op.drop_index(idx, "episodic_chains")
        except Exception:
            pass
    if _table_exists(inspector, "episodic_chains"):
        op.drop_table("episodic_chains")

    for col in ["times_retrieved", "last_retrieved_at", "successful_retrievals", "failed_retrievals"]:
        if _col_exists(inspector, "memories", col):
            op.drop_column("memories", col)
