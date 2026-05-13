"""P17: job_locks table for multi-worker distributed locking.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job_locks" not in inspector.get_table_names():
        op.create_table(
            "job_locks",
            sa.Column("job_name", sa.String(128), primary_key=True),
            sa.Column("locked_by", sa.String(256), nullable=False),
            sa.Column("locked_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(32), server_default="locked"),
        )
        op.create_index("ix_job_locks_status", "job_locks", ["status"])
        op.create_index("ix_job_locks_expires", "job_locks", ["expires_at"])


def downgrade() -> None:
    try:
        op.drop_index("ix_job_locks_expires", "job_locks")
        op.drop_index("ix_job_locks_status", "job_locks")
    except Exception:
        pass
    try:
        op.drop_table("job_locks")
    except Exception:
        pass
