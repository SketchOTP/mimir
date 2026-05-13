"""P11 graph memory: graph_nodes + graph_edges tables

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── graph_nodes ───────────────────────────────────────────────────────────
    if not _table_exists(inspector, "graph_nodes"):
        op.create_table(
            "graph_nodes",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("node_type", sa.String(32), nullable=False),
            sa.Column("entity_id", sa.String(128), nullable=False),
            sa.Column("label", sa.String(256), nullable=False),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        )
        try:
            op.create_index("ix_graph_nodes_entity", "graph_nodes", ["entity_id", "node_type"], unique=True)
        except Exception:
            pass
        try:
            op.create_index("ix_graph_nodes_project", "graph_nodes", ["project"])
        except Exception:
            pass
        try:
            op.create_index("ix_graph_nodes_type", "graph_nodes", ["node_type"])
        except Exception:
            pass

    # ── graph_edges ───────────────────────────────────────────────────────────
    if not _table_exists(inspector, "graph_edges"):
        op.create_table(
            "graph_edges",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_node_id", sa.String(64), sa.ForeignKey("graph_nodes.id"), nullable=False),
            sa.Column("target_node_id", sa.String(64), sa.ForeignKey("graph_nodes.id"), nullable=False),
            sa.Column("rel_type", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0.7"),
            sa.Column("strength", sa.Float(), server_default="1.0"),
            sa.Column("source", sa.String(64), server_default="auto"),
            sa.Column("verification_status", sa.String(32), server_default="inferred"),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        )
        try:
            op.create_index("ix_graph_edges_source", "graph_edges", ["source_node_id"])
        except Exception:
            pass
        try:
            op.create_index("ix_graph_edges_target", "graph_edges", ["target_node_id"])
        except Exception:
            pass
        try:
            op.create_index("ix_graph_edges_rel", "graph_edges", ["rel_type"])
        except Exception:
            pass
        try:
            op.create_index(
                "ix_graph_edges_src_tgt_rel",
                "graph_edges",
                ["source_node_id", "target_node_id", "rel_type"],
                unique=True,
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "graph_edges"):
        op.drop_table("graph_edges")
    if _table_exists(inspector, "graph_nodes"):
        op.drop_table("graph_nodes")
