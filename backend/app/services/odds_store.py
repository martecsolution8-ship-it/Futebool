"""Gravação idempotente de odds em `fixture_odds`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.odds import FixtureOdds


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def upsert_odds(
    db: Session,
    source: str,
    entries: Iterable[tuple[uuid.UUID, list[dict[str, Any]]]],
    *,
    touch: bool = False,
) -> int:
    """Grava odds por (jogo, bookmaker, mercado, selecção, linha, tipo). Devolve quantas mudaram.

    touch=True actualiza `updated_at` sempre que a odd é vista (conectores em tempo real usam isto
    para saber a frescura da odd); o importador de histórico usa False para não reescrever milhares de linhas.
    """
    entries = list(entries)
    fixture_ids = list({fixture_id for fixture_id, _ in entries})
    existing: dict[tuple, FixtureOdds] = {}
    for chunk in _chunks(fixture_ids, 300):
        for odd in db.scalars(
            select(FixtureOdds).where(FixtureOdds.source == source, FixtureOdds.fixture_id.in_(chunk))
        ).all():
            existing[(odd.fixture_id, odd.bookmaker, odd.market, odd.selection, round(odd.line, 2), odd.kind)] = odd

    now = datetime.now(timezone.utc)
    changed = 0
    for fixture_id, items in entries:
        for item in items:
            key = (fixture_id, item["bookmaker"], item["market"], item["selection"], round(item["line"], 2), item["kind"])
            current = existing.get(key)
            if current is None:
                odd = FixtureOdds(
                    fixture_id=fixture_id, source=source, bookmaker=item["bookmaker"], market=item["market"],
                    selection=item["selection"], line=round(item["line"], 2), kind=item["kind"], price=item["price"],
                )
                db.add(odd)
                existing[key] = odd
                changed += 1
            else:
                if abs(current.price - item["price"]) > 1e-9:
                    current.price = item["price"]
                    changed += 1
                if touch:
                    current.updated_at = now
    return changed
