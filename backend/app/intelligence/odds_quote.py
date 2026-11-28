"""Escolha da odd de mercado a usar na avaliação de valor (lógica pura, sem base de dados).

O MarBet nunca inventa odds: só usa odds reais guardadas em `fixture_odds`, recentes, e só nos
mercados em que o modelo e as casas falam da mesma coisa (1X2 e Mais/Menos 2,5 golos).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# (mercado do motor, selecção do motor) -> (mercado em fixture_odds, selecção, linha)
MARKET_MAP: dict[tuple[str, str], tuple[str, str, float]] = {
    ("1X2", "HOME"): ("1X2", "H", 0.0),
    ("1X2", "DRAW"): ("1X2", "D", 0.0),
    ("1X2", "AWAY"): ("1X2", "A", 0.0),
    ("TOTAL_GOALS", "OVER_2.5"): ("OU25", "OVER", 2.5),
    ("TOTAL_GOALS", "UNDER_2.5"): ("OU25", "UNDER", 2.5),
}

DEFAULT_BOOKMAKERS = ("bet365", "pinnacle", "market_avg")
# Para jogos futuros só interessam odds pré-jogo; "closing" só aparece quando o fornecedor a vai actualizando.
KIND_ORDER = ("pre_match", "closing", "opening")


def preferred_bookmakers() -> tuple[str, ...]:
    raw = os.getenv("ODDS_PREFERRED_BOOKMAKERS", "")
    items = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    return items or DEFAULT_BOOKMAKERS


def max_age_hours() -> float:
    try:
        return float(os.getenv("ODDS_MAX_AGE_HOURS", "48"))
    except ValueError:
        return 48.0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pick_quote(
    rows: Iterable[Any],
    market: str,
    selection: str,
    *,
    preferred: Iterable[str] | None = None,
    now: datetime | None = None,
    max_age: float | None = None,
) -> dict | None:
    target = MARKET_MAP.get((market, selection))
    if target is None:
        return None
    odds_market, odds_selection, odds_line = target
    now = now or datetime.now(timezone.utc)
    limit = timedelta(hours=max_age if max_age is not None else max_age_hours())

    fresh = []
    for row in rows:
        if row.market != odds_market or row.selection != odds_selection or abs(row.line - odds_line) > 1e-9:
            continue
        if not row.price or row.price <= 1.0:
            continue
        updated = _aware(row.updated_at)
        if updated is None or now - updated > limit:
            continue
        fresh.append(row)

    for bookmaker in (preferred if preferred is not None else preferred_bookmakers()):
        candidates = [row for row in fresh if row.bookmaker == bookmaker and row.kind in KIND_ORDER]
        if not candidates:
            continue
        best = min(candidates, key=lambda r: (KIND_ORDER.index(r.kind), -_aware(r.updated_at).timestamp()))
        return {
            "price": float(best.price),
            "bookmaker": best.bookmaker,
            "source": best.source,
            "kind": best.kind,
            "updated_at": _aware(best.updated_at).isoformat(),
        }
    return None
