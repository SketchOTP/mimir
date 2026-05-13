"""add users, api_keys tables and user_id columns to existing tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── users ─────────────────────────────────────────────────────────────────
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("email", sa.String(256), nullable=False, unique=True),
            sa.Column("display_name", sa.String(128), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── api_keys ──────────────────────────────────────────────────────────────
    if "api_keys" not in existing_tables:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
            sa.Column("name", sa.String(128), nullable=True),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("last_used_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )
        op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # ── add user_id to existing tables (nullable, no default) ─────────────────
    _add_column_if_missing(inspector, "skills", "user_id", sa.String(64))
    _add_column_if_missing(inspector, "reflections", "user_id", sa.String(64))
    _add_column_if_missing(inspector, "improvement_proposals", "user_id", sa.String(64))
    _add_column_if_missing(inspector, "approval_requests", "user_id", sa.String(64))
    _add_column_if_missing(inspector, "notifications", "user_id", sa.String(64))

    # ── extend approval_audit_log with actor identity columns ─────────────────
    _add_column_if_missing(inspector, "approval_audit_log", "actor_user_id", sa.String(64))
    _add_column_if_missing(inspector, "approval_audit_log", "actor_display_name", sa.String(128))


def _add_column_if_missing(inspector, table: str, column: str, col_type) -> None:
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        op.add_column(table, sa.Column(column, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, col in [
        ("approval_audit_log", "actor_display_name"),
        ("approval_audit_log", "actor_user_id"),
        ("notifications", "user_id"),
        ("approval_requests", "user_id"),
        ("improvement_proposals", "user_id"),
        ("reflections", "user_id"),
        ("skills", "user_id"),
    ]:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if col in existing:
            op.drop_column(table, col)

    existing_tables = inspector.get_table_names()
    if "api_keys" in existing_tables:
        op.drop_table("api_keys")
    if "users" in existing_tables:
        op.drop_table("users")
