import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TeamAlias(Base):
    """Nome alternativo de uma equipa, por fornecedor (ex.: "Man United" -> Manchester United)."""

    __tablename__ = "team_aliases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("source", "alias_normalized", name="uq_team_alias_source_name"),)


class FixtureOdds(Base):
    """Uma odd de um bookmaker para um jogo, em formato longo.

    kind: "pre_match" (odds recolhidas antes do jogo) ou "closing" (odds de fecho).
    line: 0.0 quando o mercado não tem linha (1X2); 2.5 em Over/Under; handicap em AH.
    """

    __tablename__ = "fixture_odds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fixture_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    selection: Mapped[str] = mapped_column(String(20), nullable=False)
    line: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("fixture_id", "source", "bookmaker", "market", "selection", "line", "kind", name="uq_fixture_odds_key"),
    )
