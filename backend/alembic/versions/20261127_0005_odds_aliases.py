"""Add fixture odds and team aliases.

Revision ID: 0005_odds_aliases
Revises: 0004_fixture_states
Create Date: 2026-11-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_odds_aliases"
down_revision: Union[str, None] = "0004_fixture_states"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("alias_normalized", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "alias_normalized", name="uq_team_alias_source_name"),
    )
    op.create_index("ix_team_aliases_team_id", "team_aliases", ["team_id"])

    op.create_table(
        "fixture_odds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("bookmaker", sa.String(length=50), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("selection", sa.String(length=20), nullable=False),
        sa.Column("line", sa.Float(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", "source", "bookmaker", "market", "selection", "line", "kind", name="uq_fixture_odds_key"),
    )
    op.create_index("ix_fixture_odds_fixture_id", "fixture_odds", ["fixture_id"])


def downgrade() -> None:
    op.drop_table("fixture_odds")
    op.drop_table("team_aliases")
