"""add procedural learning fields and retrieval_feedback table

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Procedural learning fields on memories ────────────────────────────────
    for col_name, col_type, default in [
        ("evidence_count", sa.Integer(), "0"),
        ("derived_from_episode_ids", sa.JSON(), None),
        ("last_success_at", sa.DateTime(), None),
        ("last_failure_at", sa.DateTime(), None),
    ]:
        if not _col_exists(inspector, "memories", col_name):
            op.add_column("memories", sa.Column(col_name, col_type, nullable=True))
            if default is not None:
                bind.execute(sa.text(
                    f"UPDATE memories SET {col_name} = {default} WHERE {col_name} IS NULL"
                ))

    # ── retrieval_feedback table ──────────────────────────────────────────────
    if not _table_exists(inspector, "retrieval_feedback"):
        op.create_table(
            "retrieval_feedback",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("memory_id", sa.String(64), nullable=False),
            sa.Column("outcome", sa.String(32), nullable=False),
            # success | failure | irrelevant | harmful
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_retrieval_feedback_memory", "retrieval_feedback", ["memory_id"])
        op.create_index("ix_retrieval_feedback_outcome", "retrieval_feedback", ["outcome"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for idx in ["ix_retrieval_feedback_outcome", "ix_retrieval_feedback_memory"]:
        try:
            op.drop_index(idx, "retrieval_feedback")
        except Exception:
            pass
    if _table_exists(inspector, "retrieval_feedback"):
        op.drop_table("retrieval_feedback")

    for col in ["evidence_count", "derived_from_episode_ids", "last_success_at", "last_failure_at"]:
        if _col_exists(inspector, "memories", col):
            op.drop_column("memories", col)
