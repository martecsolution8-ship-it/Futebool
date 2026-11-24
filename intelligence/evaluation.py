"""Avaliação dos palpites PUBLICADOS e já liquidados (funções puras).

Mede o desempenho real do MarBet: taxa de acerto, Brier, Log Loss, calibração, por mercado e por competição,
e deteta degradação recente. Palpites anulados/pendentes nunca entram aqui.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

EPS = 1e-4


@dataclass(frozen=True)
class SettledPick:
    market: str
    competition: str
    probability: float          # probabilidade PUBLICADA (original, nunca a recalculada depois)
    correct: bool
    settled_at: datetime | None = None
    model_version: str | None = None


def _clip(p: float) -> float:
    return min(max(float(p), EPS), 1 - EPS)


def brier(pairs: list[tuple[float, float]]) -> float:
    return sum((_clip(p) - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, float]]) -> float:
    return -sum(y * math.log(_clip(p)) + (1 - y) * math.log(1 - _clip(p)) for p, y in pairs) / len(pairs)


def summarize(picks: list[SettledPick]) -> dict | None:
    if not picks:
        return None
    pairs = [(p.probability, 1.0 if p.correct else 0.0) for p in picks]
    n = len(pairs)
    hit = sum(y for _, y in pairs) / n
    avg_p = sum(p for p, _ in pairs) / n
    return {
        "samples": n,
        "hit_rate": hit,
        "avg_probability": avg_p,
        "calibration_gap": avg_p - hit,       # > 0: o modelo foi demasiado otimista
        "brier_score": brier(pairs),
        "log_loss": log_loss(pairs),
    }


def calibration_table(picks: list[SettledPick], bins: int = 5) -> list[dict]:
    """Probabilidade média prevista vs frequência real, por escalão de probabilidade."""
    result = []
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        group = [p for p in picks if lo <= p.probability < hi or (k == bins - 1 and p.probability == 1.0)]
        if group:
            result.append({
                "lower": lo, "upper": hi, "count": len(group),
                "mean_probability": sum(p.probability for p in group) / len(group),
                "empirical_frequency": sum(1 for p in group if p.correct) / len(group),
            })
    return result


def expected_calibration_error(probabilities, outcomes, bins: int = 10) -> float:
    """ECE: diferença média (ponderada) entre a probabilidade prevista e a frequência real, por escalão.

    probabilities/outcomes: sequências planas (para o 1X2, as três classes de cada jogo).
    0 = calibração perfeita; valores acima de ~0,05 indicam probabilidades pouco fiáveis.
    """
    pairs = [(float(p), float(y)) for p, y in zip(probabilities, outcomes)]
    if not pairs:
        return float("nan")
    total = len(pairs)
    ece = 0.0
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        group = [(p, y) for p, y in pairs if lo <= p < hi or (k == bins - 1 and p == 1.0)]
        if group:
            ece += len(group) / total * abs(sum(p for p, _ in group) / len(group) - sum(y for _, y in group) / len(group))
    return ece


def weak_segments(by_key: dict[str, dict], min_samples: int = 20, max_gap: float = 0.10) -> list[dict]:
    """Segmentos (mercado/competição) onde o modelo falha: otimista demais ou pior que atirar uma moeda ao ar.

    Um palpite sempre a 50% tem Brier 0,25; um segmento acima disso está a fazer pior do que não saber nada.
    """
    weak = []
    for key, m in by_key.items():
        if m["samples"] < min_samples:
            continue
        reasons = []
        if m["calibration_gap"] > max_gap:
            reasons.append("probabilidades demasiado otimistas face ao resultado real")
        if m["brier_score"] > 0.25:
            reasons.append("Brier acima de 0,25 (pior do que 50% fixo)")
        if reasons:
            weak.append({"segment": key, "samples": m["samples"], "reasons": reasons})
    return weak


def detect_degradation(picks: list[SettledPick], window: int = 30, min_earlier: int = 30, threshold: float = 0.04) -> dict:
    """Compara o Brier dos últimos `window` palpites com os anteriores. Só conclui com amostra suficiente."""
    ordered = sorted((p for p in picks if p.settled_at is not None), key=lambda p: p.settled_at)
    if len(ordered) < window + min_earlier:
        return {"evaluated": False, "degraded": None,
                "note": f"Amostra insuficiente: são precisos pelo menos {window + min_earlier} palpites liquidados."}
    recent, earlier = ordered[-window:], ordered[:-window]
    brier_recent = brier([(p.probability, 1.0 if p.correct else 0.0) for p in recent])
    brier_earlier = brier([(p.probability, 1.0 if p.correct else 0.0) for p in earlier])
    hit_recent = sum(p.correct for p in recent) / len(recent)
    hit_earlier = sum(p.correct for p in earlier) / len(earlier)
    return {
        "evaluated": True,
        "degraded": bool(brier_recent - brier_earlier > threshold),
        "brier_recent": brier_recent, "brier_earlier": brier_earlier,
        "hit_rate_recent": hit_recent, "hit_rate_earlier": hit_earlier,
        "window": window,
        "note": "Degradação = Brier recente pior que o histórico anterior em mais de %.2f." % threshold,
    }


def evaluate(picks: Iterable[SettledPick]) -> dict:
    picks = list(picks)
    by_market: dict[str, list[SettledPick]] = {}
    by_competition: dict[str, list[SettledPick]] = {}
    for p in picks:
        by_market.setdefault(p.market, []).append(p)
        by_competition.setdefault(p.competition or "Desconhecida", []).append(p)
    market_summary = {k: summarize(v) for k, v in by_market.items()}
    competition_summary = {k: summarize(v) for k, v in by_competition.items()}
    return {
        "overall": summarize(picks),
        "by_market": market_summary,
        "by_competition": competition_summary,
        "calibration": calibration_table(picks),
        "weak_markets": weak_segments(market_summary),
        "weak_competitions": weak_segments(competition_summary),
        "degradation": detect_degradation(picks),
    }
