"""P12 simulation engine: simulation_plans, simulation_runs, forecast_calibration tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── simulation_plans ──────────────────────────────────────────────────────
    if not _table_exists(inspector, "simulation_plans"):
        op.create_table(
            "simulation_plans",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), server_default="draft"),
            sa.Column("steps", sa.JSON(), nullable=True),
            sa.Column("risk_estimate", sa.Float(), server_default="0.0"),
            sa.Column("confidence_estimate", sa.Float(), server_default="0.5"),
            sa.Column("rollback_options", sa.JSON(), nullable=True),
            sa.Column("expected_outcomes", sa.JSON(), nullable=True),
            sa.Column("approval_required", sa.Boolean(), server_default="0"),
            sa.Column("approval_id", sa.String(64), nullable=True),
            sa.Column("graph_valid", sa.Boolean(), server_default="1"),
            sa.Column("graph_errors", sa.JSON(), nullable=True),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        )
        for idx_name, cols, unique in [
            ("ix_simulation_plans_project", ["project"], False),
            ("ix_simulation_plans_status", ["status"], False),
        ]:
            try:
                op.create_index(idx_name, "simulation_plans", cols, unique=unique)
            except Exception:
                pass

    # ── simulation_runs ───────────────────────────────────────────────────────
    if not _table_exists(inspector, "simulation_runs"):
        op.create_table(
            "simulation_runs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("plan_id", sa.String(64), sa.ForeignKey("simulation_plans.id"), nullable=False),
            sa.Column("simulation_type", sa.String(32), server_default="full"),
            sa.Column("counterfactual_description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), server_default="complete"),
            sa.Column("paths", sa.JSON(), nullable=True),
            sa.Column("best_path_id", sa.String(128), nullable=True),
            sa.Column("success_probability", sa.Float(), server_default="0.5"),
            sa.Column("risk_score", sa.Float(), server_default="0.5"),
            sa.Column("confidence_score", sa.Float(), server_default="0.1"),
            sa.Column("expected_failure_modes", sa.JSON(), nullable=True),
            sa.Column("historical_memories_used", sa.JSON(), nullable=True),
            sa.Column("tokens_used", sa.Integer(), server_default="0"),
            sa.Column("depth_reached", sa.Integer(), server_default="0"),
            sa.Column("actual_outcome", sa.String(32), nullable=True),
            sa.Column("forecast_was_correct", sa.Boolean(), nullable=True),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        for idx_name, cols in [
            ("ix_simulation_runs_plan", ["plan_id"]),
            ("ix_simulation_runs_type", ["simulation_type"]),
            ("ix_simulation_runs_project", ["project"]),
        ]:
            try:
                op.create_index(idx_name, "simulation_runs", cols)
            except Exception:
                pass

    # ── forecast_calibration ──────────────────────────────────────────────────
    if not _table_exists(inspector, "forecast_calibration"):
        op.create_table(
            "forecast_calibration",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("project", sa.String(128), nullable=True),
            sa.Column("period", sa.String(32), server_default="daily"),
            sa.Column("total_forecasts", sa.Integer(), server_default="0"),
            sa.Column("correct_forecasts", sa.Integer(), server_default="0"),
            sa.Column("forecast_accuracy", sa.Float(), server_default="0.0"),
            sa.Column("overconfidence_rate", sa.Float(), server_default="0.0"),
            sa.Column("underconfidence_rate", sa.Float(), server_default="0.0"),
            sa.Column("mean_prediction_error", sa.Float(), server_default="0.0"),
            sa.Column("computed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        )
        for idx_name, cols in [
            ("ix_forecast_calibration_project", ["project"]),
            ("ix_forecast_calibration_period", ["period"]),
        ]:
            try:
                op.create_index(idx_name, "forecast_calibration", cols)
            except Exception:
                pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("forecast_calibration", "simulation_runs", "simulation_plans"):
        if _table_exists(inspector, table):
            op.drop_table(table)
