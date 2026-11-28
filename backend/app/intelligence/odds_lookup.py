"""Carrega as odds guardadas de um jogo e escolhe a que entra na avaliação de valor."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence.odds_quote import pick_quote
from app.models.odds import FixtureOdds


def quote_for(db: Session, cache: dict[uuid.UUID, list[FixtureOdds]], fixture_id: uuid.UUID, market: str, selection: str) -> dict | None:
    rows = cache.get(fixture_id)
    if rows is None:
        rows = list(db.scalars(select(FixtureOdds).where(FixtureOdds.fixture_id == fixture_id)).all())
        cache[fixture_id] = rows
    return pick_quote(rows, market, selection)
