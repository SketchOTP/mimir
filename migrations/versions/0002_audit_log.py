"""add approval_audit_log table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0001 runs Base.metadata.create_all(checkfirst=True), so the table may
    # already exist in environments that started from 0001.  Skip if present.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "approval_audit_log" in inspector.get_table_names():
        return

    op.create_table(
        "approval_audit_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("approval_id", sa.String(64), nullable=False, index=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "approval_audit_log" in inspector.get_table_names():
        op.drop_table("approval_audit_log")
