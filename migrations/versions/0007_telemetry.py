"""add retrieval_sessions and telemetry_snapshots tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── retrieval_sessions ────────────────────────────────────────────────────
    if not _table_exists(inspector, "retrieval_sessions"):
        op.create_table(
            "retrieval_sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("session_id", sa.String(64), nullable=True),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("retrieved_memory_ids", sa.JSON(), nullable=True),
            sa.Column("result_count", sa.Integer(), server_default="0"),
            sa.Column("token_cost", sa.Integer(), server_default="0"),
            sa.Column("task_outcome", sa.String(32), nullable=True),
            sa.Column("rollback_id", sa.String(64), nullable=True),
            sa.Column("has_correction", sa.Boolean(), server_default="0"),
            sa.Column("has_harmful_outcome", sa.Boolean(), server_default="0"),
            sa.Column("inference_applied", sa.Boolean(), server_default="0"),
            sa.Column("relevance_score", sa.Float(), nullable=True),
            sa.Column("usefulness_score", sa.Float(), nullable=True),
            sa.Column("harmfulness_score", sa.Float(), nullable=True),
            sa.Column("agreement_score", sa.Float(), nullable=True),
            sa.Column("token_efficiency_score", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_retrieval_sessions_project", "retrieval_sessions", ["project"])
        op.create_index("ix_retrieval_sessions_session", "retrieval_sessions", ["session_id"])
        op.create_index("ix_retrieval_sessions_outcome", "retrieval_sessions", ["task_outcome"])
        op.create_index("ix_retrieval_sessions_inference", "retrieval_sessions", ["inference_applied"])

    # ── telemetry_snapshots ───────────────────────────────────────────────────
    if not _table_exists(inspector, "telemetry_snapshots"):
        op.create_table(
            "telemetry_snapshots",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("metric_name", sa.String(128), nullable=False),
            sa.Column("metric_value", sa.Float(), nullable=False),
            sa.Column("period", sa.String(32), server_default="daily"),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_telemetry_name_project", "telemetry_snapshots", ["metric_name", "project"])
        op.create_index("ix_telemetry_recorded_at", "telemetry_snapshots", ["recorded_at"])


def downgrade() -> None:
    for idx in [
        "ix_telemetry_recorded_at",
        "ix_telemetry_name_project",
    ]:
        try:
            op.drop_index(idx, "telemetry_snapshots")
        except Exception:
            pass
    try:
        op.drop_table("telemetry_snapshots")
    except Exception:
        pass

    for idx in [
        "ix_retrieval_sessions_inference",
        "ix_retrieval_sessions_outcome",
        "ix_retrieval_sessions_session",
        "ix_retrieval_sessions_project",
    ]:
        try:
            op.drop_index(idx, "retrieval_sessions")
        except Exception:
            pass
    try:
        op.drop_table("retrieval_sessions")
    except Exception:
        pass
