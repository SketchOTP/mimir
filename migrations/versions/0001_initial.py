"""initial schema — creates all Mimir tables

Revision ID: 0001
Revises:
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all Mimir tables.  Skips tables that already exist (checkfirst=True)
    so this migration is safe to run against both fresh and pre-existing databases."""
    from storage.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    """Drop all Mimir tables (destructive — back up data first)."""
    from storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
