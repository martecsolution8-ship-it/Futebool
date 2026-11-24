"""Add normalized fixture states.

Revision ID: 0004_fixture_states
Revises: 0003_intelligence
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_fixture_states"
down_revision: Union[str, None] = "0003_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE fixture_status ADD VALUE IF NOT EXISTS 'INTERVAL'")
        op.execute("ALTER TYPE fixture_status ADD VALUE IF NOT EXISTS 'SUSPENDED'")
        op.execute("ALTER TYPE fixture_status ADD VALUE IF NOT EXISTS 'UNKNOWN'")


def downgrade() -> None:
    # PostgreSQL enum values are intentionally retained for historical rows.
    pass
