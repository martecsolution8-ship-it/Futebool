"""Regras puras de imutabilidade dos palpites publicados (sem base de dados)."""
from __future__ import annotations

FROZEN_COLUMNS = ("fixture_id", "model_version_id", "market", "selection", "probability", "confidence", "data_quality")
FROZEN_RATIONALE_KEYS = ("published_pick", "published_at", "published_snapshot", "first_probability")


def check_published_update(old: dict, new: dict, old_rationale: dict, new_rationale: dict) -> list[str]:
    """Devolve a lista de campos que violam a imutabilidade (vazia = alteração permitida).

    old/new: valores das colunas congeladas antes/depois. *_rationale: fundamentação antes/depois.
    Só se aplica se o palpite JÁ estava publicado antes da alteração.
    """
    if not (old_rationale or {}).get("published_pick"):
        return []
    violations = [name for name in FROZEN_COLUMNS if name in new and old.get(name) != new.get(name)]
    for key in FROZEN_RATIONALE_KEYS:
        if (old_rationale or {}).get(key) != (new_rationale or {}).get(key):
            violations.append(f"rationale.{key}")
    return violations
