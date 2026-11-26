"""MarBet Intelligence Engine persistence.

Revision ID: 0003_intelligence
Revises: 0002_sports_data
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_intelligence"
down_revision: Union[str, None] = "0002_sports_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("model_versions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("code", sa.String(50), nullable=False), sa.Column("version", sa.String(30), nullable=False), sa.Column("status", sa.Enum("DEVELOPMENT", "VALIDATED", "ACTIVE", "RETIRED", name="model_status"), nullable=False), sa.Column("algorithm", sa.String(100), nullable=False), sa.Column("parameters", sa.JSON(), nullable=False), sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True), sa.Column("data_from", sa.DateTime(timezone=True), nullable=True), sa.Column("data_to", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("code", "version", name="uq_model_code_version"))

    op.create_table("training_runs", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("model_version_id", sa.Uuid(), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("train_samples", sa.Integer(), nullable=False), sa.Column("validation_samples", sa.Integer(), nullable=False), sa.Column("test_samples", sa.Integer(), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("error_message", sa.Text(), nullable=True), sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_training_runs_model_version_id", "training_runs", ["model_version_id"])

    op.create_table("evaluation_metrics", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("model_version_id", sa.Uuid(), nullable=False), sa.Column("competition_id", sa.Uuid(), nullable=True), sa.Column("market", sa.String(50), nullable=False), sa.Column("sample_count", sa.Integer(), nullable=False), sa.Column("brier_score", sa.Float(), nullable=True), sa.Column("log_loss", sa.Float(), nullable=True), sa.Column("accuracy", sa.Float(), nullable=True), sa.Column("calibration", sa.JSON(), nullable=False), sa.Column("period_from", sa.DateTime(timezone=True), nullable=True), sa.Column("period_to", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_evaluation_metrics_model_version_id", "evaluation_metrics", ["model_version_id"])
    op.create_index("ix_evaluation_metrics_competition_id", "evaluation_metrics", ["competition_id"])

    op.create_table("feature_snapshots", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("fixture_id", sa.Uuid(), nullable=False), sa.Column("feature_version", sa.String(30), nullable=False), sa.Column("values", sa.JSON(), nullable=False), sa.Column("data_quality", sa.Float(), nullable=False), sa.Column("missing_features", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("fixture_id"))

    op.create_table("predictions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("fixture_id", sa.Uuid(), nullable=False), sa.Column("model_version_id", sa.Uuid(), nullable=False), sa.Column("market", sa.String(50), nullable=False), sa.Column("selection", sa.String(100), nullable=False), sa.Column("probability", sa.Float(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("data_quality", sa.Float(), nullable=False), sa.Column("rationale", sa.JSON(), nullable=False), sa.Column("status", sa.Enum("PENDING", "SETTLED", "VOID", name="prediction_status"), nullable=False), sa.Column("actual_outcome", sa.String(100), nullable=True), sa.Column("is_correct", sa.Boolean(), nullable=True), sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True), sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("fixture_id", "market", "selection", "model_version_id", name="uq_prediction_fixture_market_model"))
    op.create_index("ix_predictions_fixture_id", "predictions", ["fixture_id"])
    op.create_index("ix_predictions_model_version_id", "predictions", ["model_version_id"])

    op.create_table("engine_runs", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("operation", sa.String(50), nullable=False), sa.Column("status", sa.Enum("RUNNING", "SUCCESS", "PARTIAL", "FAILED", name="engine_run_status"), nullable=False), sa.Column("model_version_id", sa.Uuid(), nullable=True), sa.Column("records_processed", sa.Integer(), nullable=False), sa.Column("records_created", sa.Integer(), nullable=False), sa.Column("error_count", sa.Integer(), nullable=False), sa.Column("error_message", sa.Text(), nullable=True), sa.Column("metadata_json", sa.JSON(), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))

    op.create_table("backtest_runs", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("model_version_id", sa.Uuid(), nullable=False), sa.Column("initial_train_matches", sa.Integer(), nullable=False), sa.Column("test_matches", sa.Integer(), nullable=False), sa.Column("folds", sa.Integer(), nullable=False), sa.Column("summary", sa.JSON(), nullable=False), sa.Column("period_from", sa.DateTime(timezone=True), nullable=True), sa.Column("period_to", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_backtest_runs_model_version_id", "backtest_runs", ["model_version_id"])


def downgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("engine_runs")
    op.drop_table("predictions")
    op.drop_table("feature_snapshots")
    op.drop_table("evaluation_metrics")
    op.drop_table("training_runs")
    op.drop_table("model_versions")
