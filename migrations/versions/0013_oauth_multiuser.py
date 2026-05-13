"""P20: OAuth 2.1/PKCE multi-user auth — OAuth tables + User.role + User.last_login_at.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    existing_users_cols = {c["name"] for c in inspector.get_columns("users")} if "users" in existing_tables else set()

    # ── Extend users table ──────────────────────────────────────────────────
    if "role" not in existing_users_cols:
        op.add_column("users", sa.Column("role", sa.String(32), server_default="user", nullable=False))
    if "last_login_at" not in existing_users_cols:
        op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    # ── OAuth clients ───────────────────────────────────────────────────────
    if "oauth_clients" not in existing_tables:
        op.create_table(
            "oauth_clients",
            sa.Column("client_id", sa.String(128), primary_key=True),
            sa.Column("client_name", sa.String(256), nullable=True),
            sa.Column("redirect_uris", sa.Text(), nullable=False),
            sa.Column("grant_types", sa.Text(), server_default='["authorization_code","refresh_token"]'),
            sa.Column("response_types", sa.Text(), server_default='["code"]'),
            sa.Column("is_public", sa.Boolean(), server_default="1"),
            sa.Column("is_active", sa.Boolean(), server_default="1"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    # ── OAuth authorization codes ───────────────────────────────────────────
    if "oauth_authorization_codes" not in existing_tables:
        op.create_table(
            "oauth_authorization_codes",
            sa.Column("code", sa.String(128), primary_key=True),
            sa.Column("client_id", sa.String(128), nullable=False, index=True),
            sa.Column("user_id", sa.String(64), nullable=False, index=True),
            sa.Column("redirect_uri", sa.Text(), nullable=False),
            sa.Column("scope", sa.String(512), nullable=True),
            sa.Column("code_challenge", sa.String(256), nullable=False),
            sa.Column("code_challenge_method", sa.String(10), server_default="S256"),
            sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("used", sa.Boolean(), server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_oauth_codes_client", "oauth_authorization_codes", ["client_id"])
        op.create_index("ix_oauth_codes_user", "oauth_authorization_codes", ["user_id"])
        op.create_index("ix_oauth_codes_expires", "oauth_authorization_codes", ["expires_at"])

    # ── OAuth access tokens ─────────────────────────────────────────────────
    if "oauth_tokens" not in existing_tables:
        op.create_table(
            "oauth_tokens",
            sa.Column("token_hash", sa.String(128), primary_key=True),
            sa.Column("client_id", sa.String(128), nullable=False, index=True),
            sa.Column("user_id", sa.String(64), nullable=False, index=True),
            sa.Column("scope", sa.String(512), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True, index=True),
            sa.Column("revoked", sa.Boolean(), server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_oauth_tokens_client", "oauth_tokens", ["client_id"])
        op.create_index("ix_oauth_tokens_user", "oauth_tokens", ["user_id"])
        op.create_index("ix_oauth_tokens_expires", "oauth_tokens", ["expires_at"])

    # ── OAuth refresh tokens ────────────────────────────────────────────────
    if "oauth_refresh_tokens" not in existing_tables:
        op.create_table(
            "oauth_refresh_tokens",
            sa.Column("token_hash", sa.String(128), primary_key=True),
            sa.Column("access_token_hash", sa.String(128), nullable=True, index=True),
            sa.Column("client_id", sa.String(128), nullable=False, index=True),
            sa.Column("user_id", sa.String(64), nullable=False, index=True),
            sa.Column("scope", sa.String(512), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True, index=True),
            sa.Column("revoked", sa.Boolean(), server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_oauth_refresh_client", "oauth_refresh_tokens", ["client_id"])
        op.create_index("ix_oauth_refresh_user", "oauth_refresh_tokens", ["user_id"])
        op.create_index("ix_oauth_refresh_access", "oauth_refresh_tokens", ["access_token_hash"])
        op.create_index("ix_oauth_refresh_expires", "oauth_refresh_tokens", ["expires_at"])


def downgrade() -> None:
    for idx, tbl in [
        ("ix_oauth_refresh_expires", "oauth_refresh_tokens"),
        ("ix_oauth_refresh_access", "oauth_refresh_tokens"),
        ("ix_oauth_refresh_user", "oauth_refresh_tokens"),
        ("ix_oauth_refresh_client", "oauth_refresh_tokens"),
        ("ix_oauth_tokens_expires", "oauth_tokens"),
        ("ix_oauth_tokens_user", "oauth_tokens"),
        ("ix_oauth_tokens_client", "oauth_tokens"),
        ("ix_oauth_codes_expires", "oauth_authorization_codes"),
        ("ix_oauth_codes_user", "oauth_authorization_codes"),
        ("ix_oauth_codes_client", "oauth_authorization_codes"),
    ]:
        try:
            op.drop_index(idx, tbl)
        except Exception:
            pass
    for tbl in ("oauth_refresh_tokens", "oauth_tokens", "oauth_authorization_codes", "oauth_clients"):
        try:
            op.drop_table(tbl)
        except Exception:
            pass
    # Remove added columns (SQLite batch mode)
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            try:
                batch_op.drop_column("last_login_at")
            except Exception:
                pass
            try:
                batch_op.drop_column("role")
            except Exception:
                pass
    else:
        try:
            op.drop_column("users", "last_login_at")
            op.drop_column("users", "role")
        except Exception:
            pass
