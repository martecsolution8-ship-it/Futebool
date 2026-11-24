"""Garantia de que um palpite PUBLICADO nunca é alterado retroativamente.

Depois de publicado (rationale["published_pick"] = True) só podem mudar os campos de liquidação:
status, actual_outcome, is_correct, settled_at. Qualquer outra alteração (probabilidade, mercado,
seleção, modelo, jogo, confiança, qualidade dos dados, fundamentação/cópia original) é recusada
ANTES de ir para a base de dados.

`check_published_update` é uma função pura (testável sem base de dados); o listener do SQLAlchemy
apenas a aplica a cada Prediction modificada no flush.
"""
from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.intelligence import Prediction
from intelligence.published_rules import FROZEN_COLUMNS, check_published_update  # noqa: F401

class ImmutablePredictionError(RuntimeError):
    """Tentativa de alterar um palpite já publicado."""


@event.listens_for(Session, "before_flush")
def _protect_published_predictions(session: Session, flush_context, instances) -> None:
    for obj in list(session.dirty):
        if not isinstance(obj, Prediction):
            continue
        state = inspect(obj)
        old: dict = {}
        new: dict = {}
        for name in FROZEN_COLUMNS:
            history = state.attrs[name].history
            if history.has_changes():
                old[name] = history.deleted[0] if history.deleted else None
                new[name] = history.added[0] if history.added else getattr(obj, name)
        rationale_history = state.attrs.rationale.history
        if rationale_history.deleted:
            old_rationale = rationale_history.deleted[0] or {}
            new_rationale = rationale_history.added[0] if rationale_history.added else (obj.rationale or {})
        else:
            old_rationale = obj.rationale or {}
            new_rationale = old_rationale
        violations = check_published_update(old, new, old_rationale, new_rationale)
        if violations:
            raise ImmutablePredictionError(
                f"Palpite publicado {obj.id} é imutável; campos alterados: {', '.join(violations)}")
