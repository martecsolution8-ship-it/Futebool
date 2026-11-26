"""Sports data entities and synchronization metadata.

Revision ID: 0002_sports_data
Revises: 0001_initial
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_sports_data"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("competitions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(length=200), nullable=False), sa.Column("country", sa.String(length=100), nullable=True), sa.Column("code", sa.String(length=20), nullable=True), sa.Column("type", sa.String(length=30), nullable=False), sa.Column("external_ids", sa.JSON(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_competitions_code", "competitions", ["code"])

    op.create_table("seasons", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("competition_id", sa.Uuid(), nullable=False), sa.Column("label", sa.String(length=20), nullable=False), sa.Column("start_date", sa.DateTime(timezone=True), nullable=True), sa.Column("end_date", sa.DateTime(timezone=True), nullable=True), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("external_ids", sa.JSON(), nullable=False), sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("competition_id", "label", name="uq_season_competition_label"))
    op.create_index("ix_seasons_competition_id", "seasons", ["competition_id"])

    op.create_table("teams", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(length=200), nullable=False), sa.Column("short_name", sa.String(length=100), nullable=True), sa.Column("tla", sa.String(length=10), nullable=True), sa.Column("country", sa.String(length=100), nullable=True), sa.Column("crest_url", sa.String(length=500), nullable=True), sa.Column("external_ids", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.PrimaryKeyConstraint("id"))

    op.create_table("players", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("team_id", sa.Uuid(), nullable=True), sa.Column("name", sa.String(length=200), nullable=False), sa.Column("position", sa.String(length=50), nullable=True), sa.Column("nationality", sa.String(length=100), nullable=True), sa.Column("external_ids", sa.JSON(), nullable=False), sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_players_team_id", "players", ["team_id"])

    op.create_table("fixtures", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("competition_id", sa.Uuid(), nullable=False), sa.Column("season_id", sa.Uuid(), nullable=False), sa.Column("home_team_id", sa.Uuid(), nullable=False), sa.Column("away_team_id", sa.Uuid(), nullable=False), sa.Column("provider", sa.String(length=50), nullable=False), sa.Column("provider_fixture_id", sa.String(length=100), nullable=False), sa.Column("round", sa.String(length=100), nullable=True), sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.Enum("SCHEDULED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED", name="fixture_status"), nullable=False), sa.Column("home_score", sa.Integer(), nullable=True), sa.Column("away_score", sa.Integer(), nullable=True), sa.Column("home_score_halftime", sa.Integer(), nullable=True), sa.Column("away_score_halftime", sa.Integer(), nullable=True), sa.Column("statistics", sa.JSON(), nullable=False), sa.Column("raw_hash", sa.String(length=64), nullable=False), sa.Column("first_synced_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("provider", "provider_fixture_id", name="uq_fixture_provider_external"))
    op.create_index("ix_fixtures_competition_id", "fixtures", ["competition_id"])
    op.create_index("ix_fixtures_season_id", "fixtures", ["season_id"])
    op.create_index("ix_fixtures_home_team_id", "fixtures", ["home_team_id"])
    op.create_index("ix_fixtures_away_team_id", "fixtures", ["away_team_id"])
    op.create_index("ix_fixtures_kickoff_at", "fixtures", ["kickoff_at"])

    op.create_table("sync_runs", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("provider", sa.String(length=50), nullable=False), sa.Column("operation", sa.String(length=100), nullable=False), sa.Column("status", sa.Enum("RUNNING", "SUCCESS", "PARTIAL", "FAILED", name="sync_status"), nullable=False), sa.Column("records_received", sa.Integer(), nullable=False), sa.Column("records_written", sa.Integer(), nullable=False), sa.Column("error_count", sa.Integer(), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("error_message", sa.Text(), nullable=True), sa.Column("metadata_json", sa.JSON(), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_sync_runs_provider", "sync_runs", ["provider"])

    op.create_table("api_usage", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("provider", sa.String(length=50), nullable=False), sa.Column("endpoint", sa.String(length=200), nullable=False), sa.Column("request_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.Column("http_status", sa.Integer(), nullable=True), sa.Column("success", sa.Boolean(), nullable=False), sa.Column("response_bytes", sa.Integer(), nullable=False), sa.Column("error_message", sa.Text(), nullable=True), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_api_usage_provider", "api_usage", ["provider"])
    op.create_index("ix_api_usage_request_at", "api_usage", ["request_at"])

    op.create_table("api_cache", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("cache_key", sa.String(length=500), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("cache_key"))
    op.create_index("ix_api_cache_cache_key", "api_cache", ["cache_key"])
    op.create_index("ix_api_cache_expires_at", "api_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_table("api_cache")
    op.drop_table("api_usage")
    op.drop_table("sync_runs")
    op.drop_table("fixtures")
    op.drop_table("players")
    op.drop_table("teams")
    op.drop_table("seasons")
    op.drop_table("competitions")
