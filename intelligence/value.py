"""Probabilidade implícita, margem da casa e valor esperado.

Só funciona com odds REAIS fornecidas de fora. O MarBet não inventa odds: sem odds válidas estas
funções devolvem None e a análise limita-se a probabilidades (sem afirmar que existe "valor").
"""
from __future__ import annotations

import math


def valid_odds(odds) -> float | None:
    """Odds decimais europeias válidas: finitas e > 1.0. Qualquer outra coisa -> None."""
    try:
        value = float(odds)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 1.0 else None


def implied_probability(odds) -> float | None:
    value = valid_odds(odds)
    return None if value is None else 1.0 / value


def remove_margin(odds_by_selection: dict[str, float]) -> dict[str, float] | None:
    """Probabilidades 'justas' de um mercado completo (todas as seleções mutuamente exclusivas).

    Normalização proporcional: p_i = (1/odd_i) / soma(1/odd_j). A soma original menos 1 é a margem da casa.
    Devolve None se faltar alguma odd válida (um mercado incompleto não permite retirar a margem).
    """
    if not odds_by_selection:
        return None
    implied = {}
    for selection, odds in odds_by_selection.items():
        p = implied_probability(odds)
        if p is None:
            return None
        implied[selection] = p
    total = sum(implied.values())
    return {selection: p / total for selection, p in implied.items()}


def bookmaker_margin(odds_by_selection: dict[str, float]) -> float | None:
    implied = [implied_probability(o) for o in odds_by_selection.values()]
    if not implied or any(p is None for p in implied):
        return None
    return sum(implied) - 1.0


def expected_value(probability: float, odds) -> float | None:
    """EV por unidade apostada: p * odd - 1. Positivo = a odd paga mais do que o risco estimado."""
    value = valid_odds(odds)
    try:
        p = float(probability)
    except (TypeError, ValueError):
        return None
    if value is None or not math.isfinite(p) or not 0.0 <= p <= 1.0:
        return None
    return p * value - 1.0


def assess_value(probability: float, odds, min_ev: float = 0.03) -> dict:
    """Avaliação de valor. Sem odds válidas: `assessed` = False e nenhuma afirmação de valor."""
    ev = expected_value(probability, odds)
    if ev is None:
        return {"assessed": False, "has_value": None, "expected_value": None, "implied_probability": None,
                "note": "Sem odds fiáveis: o MarBet não avalia valor de aposta, apenas probabilidade estimada."}
    implied = implied_probability(odds)
    return {"assessed": True, "has_value": bool(ev >= min_ev), "expected_value": ev,
            "implied_probability": implied, "edge_vs_implied": float(probability) - implied,
            "note": "Valor esperado calculado com as odds fornecidas; não garante resultado."}
