"""Seleção de prognósticos, deduplicação de jogos e métricas de desempenho.

Funções puras (sem base de dados), usadas pelo motor e pela API:

  - dedupe_matches: remove o mesmo jogo vindo de dois fornecedores.
  - choose_pick:    escolhe UM mercado por jogo, só se for estatisticamente interessante.
  - summarize_settled: taxa de acerto, Brier e Log Loss por mercado, só de prognósticos publicados.

Nada aqui inventa dados: se não houver base estatística suficiente, devolve None / vazio.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


# ----------------------------------------------------------------------------- deduplicação

def dedupe_matches(
    items: Iterable[T],
    home: Callable[[T], object],
    away: Callable[[T], object],
    kickoff: Callable[[T], datetime],
    rank: Callable[[T], float] = lambda item: 0.0,
    window: timedelta = timedelta(hours=6),
) -> list[T]:
    """Mantém um único registo por (casa, fora, hora aproximada). Devolve ordenado por data.

    `rank` decide qual registo fica (maior vence; ex.: o que já tem resultado).
    Duas equipas não jogam entre si duas vezes na mesma janela, por isso é seguro.
    """
    groups: dict[tuple, list[T]] = {}
    for item in items:
        groups.setdefault((home(item), away(item)), []).append(item)

    kept: list[T] = []
    for group in groups.values():
        group.sort(key=lambda it: (kickoff(it), -rank(it)))
        cluster: list[T] = []
        for item in group:
            if cluster and kickoff(item) - kickoff(cluster[-1]) > window:
                kept.append(max(cluster, key=rank))
                cluster = []
            cluster.append(item)
        if cluster:
            kept.append(max(cluster, key=rank))
    kept.sort(key=kickoff)
    return kept


# ----------------------------------------------------------------------------- escolha do prognóstico

@dataclass(frozen=True)
class PickCandidate:
    market: str
    selection: str
    probability: float
    base_rate: float       # frequência histórica do mesmo resultado (da competição, encolhida para a global)
    confidence: float      # qualidade dos dados x validação do modelo (NÃO é a probabilidade)
    data_quality: float

    @property
    def edge(self) -> float:
        return self.probability - self.base_rate


def choose_pick(
    candidates: Iterable[PickCandidate],
    min_probability: float = 0.55,
    min_edge: float = 0.03,
    min_quality: float = 0.40,
) -> PickCandidate | None:
    """Um mercado por jogo, ou nenhum.

    Só é elegível quem tem probabilidade mínima, dados suficientes e probabilidade
    acima da frequência histórica (o modelo tem de "saber" algo que a média não sabe).
    Como só sai um mercado por jogo, nunca há previsões contraditórias (Mais e Menos de 2,5).
    """
    eligible = [
        c for c in candidates
        if c.data_quality >= min_quality and c.probability >= min_probability and c.edge >= min_edge
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.edge * (0.5 + 0.5 * c.confidence), c.probability))


def pick_block_reason(
    candidate: PickCandidate,
    *,
    market_validated: bool,
    min_probability: float = 0.55,
    min_edge: float = 0.03,
    min_quality: float = 0.40,
) -> str | None:
    """Porque é que um candidato NÃO pode ser publicado (None = cumpre todos os critérios)."""
    if not market_validated:
        return "MERCADO_NAO_VALIDADO"
    if candidate.data_quality < min_quality:
        return "DADOS_INSUFICIENTES"
    if candidate.probability < min_probability:
        return "PROBABILIDADE_BAIXA"
    if candidate.edge < min_edge:
        return "SEM_VANTAGEM_SOBRE_A_MEDIA"
    return None


# ----------------------------------------------------------------------------- desempenho

def summarize_settled(rows: Iterable[tuple[str, float, bool]]) -> dict[str, dict]:
    """rows: (mercado, probabilidade publicada, acertou). Devolve métricas por mercado e total.

    Brier = média de (p - y)^2 ; Log Loss = -média de y*ln(p) + (1-y)*ln(1-p), sobre o evento prognosticado.
    """
    by_market: dict[str, list[tuple[float, float]]] = {}
    for market, probability, correct in rows:
        by_market.setdefault(market, []).append((min(max(float(probability), 1e-4), 1 - 1e-4), 1.0 if correct else 0.0))
        by_market.setdefault("TOTAL", []).append(by_market[market][-1])

    result: dict[str, dict] = {}
    for market, pairs in by_market.items():
        n = len(pairs)
        result[market] = {
            "samples": n,
            "hit_rate": sum(y for _, y in pairs) / n,
            "avg_probability": sum(p for p, _ in pairs) / n,
            "brier_score": sum((p - y) ** 2 for p, y in pairs) / n,
            "log_loss": -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in pairs) / n,
        }
    return result
