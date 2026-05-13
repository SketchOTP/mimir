"""add temporal and trust fields to memories table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Temporal fields ───────────────────────────────────────────────────────
    _add_col(inspector, "memories", "valid_from", sa.DateTime, nullable=True)
    _add_col(inspector, "memories", "valid_to", sa.DateTime, nullable=True)
    _add_col(inspector, "memories", "superseded_by", sa.String(64), nullable=True)
    _add_col(inspector, "memories", "memory_state", sa.String(20),
             nullable=True, server_default="active")
    _add_col(inspector, "memories", "last_verified_at", sa.DateTime, nullable=True)

    # ── Trust / provenance fields ─────────────────────────────────────────────
    _add_col(inspector, "memories", "trust_score", sa.Float,
             nullable=True, server_default="0.7")
    _add_col(inspector, "memories", "source_type", sa.String(64), nullable=True)
    _add_col(inspector, "memories", "source_id", sa.String(64), nullable=True)
    _add_col(inspector, "memories", "created_by", sa.String(64), nullable=True)
    _add_col(inspector, "memories", "verified_by", sa.String(64), nullable=True)
    _add_col(inspector, "memories", "verification_status", sa.String(32),
             nullable=True, server_default="trusted_system_observed")
    _add_col(inspector, "memories", "confidence", sa.Float,
             nullable=True, server_default="0.7")
    _add_col(inspector, "memories", "poisoning_flags", sa.JSON, nullable=True)

    # ── Backfill existing rows ─────────────────────────────────────────────────
    # valid_from mirrors created_at for pre-migration memories
    bind.execute(sa.text(
        "UPDATE memories SET valid_from = created_at WHERE valid_from IS NULL"
    ))
    # Ensure state and trust defaults are written to any NULLs
    bind.execute(sa.text(
        "UPDATE memories SET memory_state = 'active' WHERE memory_state IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE memories SET verification_status = 'trusted_system_observed' "
        "WHERE verification_status IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE memories SET trust_score = 0.7 WHERE trust_score IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE memories SET confidence = 0.7 WHERE confidence IS NULL"
    ))

    # ── Index on memory_state for fast recall filtering ───────────────────────
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("memories")}
    if "ix_memories_state" not in existing_indexes:
        op.create_index("ix_memories_state", "memories", ["memory_state"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for col in [
        "valid_from", "valid_to", "superseded_by", "memory_state", "last_verified_at",
        "trust_score", "source_type", "source_id", "created_by", "verified_by",
        "verification_status", "confidence", "poisoning_flags",
    ]:
        if col in {c["name"] for c in inspector.get_columns("memories")}:
            op.drop_column("memories", col)

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("memories")}
    if "ix_memories_state" in existing_indexes:
        op.drop_index("ix_memories_state", table_name="memories")


def _add_col(inspector, table: str, column: str, col_type, *, nullable: bool = True,
             server_default=None) -> None:
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        op.add_column(table, sa.Column(column, col_type, nullable=nullable,
                                       server_default=server_default))
