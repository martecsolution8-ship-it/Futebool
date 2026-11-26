"""Classificação objetiva da confiança: BAIXA / MODERADA / ELEVADA.

A confiança NÃO depende de a probabilidade ser alta. Resulta de:
  - qualidade dos dados (jogos históricos disponíveis das duas equipas);
  - validação fora da amostra do mercado (ganho de log loss sobre a média histórica + nº de jogos testados);
  - calibração das probabilidades (validada em dados que o ajuste não viu);
  - concordância entre modelos (Dixon-Coles vs Elo no 1X2; bruto vs calibrado nos restantes);
  - vantagem sobre a média histórica.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LOW, MODERATE, HIGH = "LOW", "MODERATE", "HIGH"
LEVEL_LABELS = {LOW: "Confiança baixa", MODERATE: "Confiança moderada", HIGH: "Confiança elevada"}

W_DATA, W_VALIDATION, W_CALIBRATION, W_AGREEMENT, W_EDGE = 0.30, 0.30, 0.15, 0.15, 0.10


@dataclass(frozen=True)
class ConfidenceResult:
    level: str
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def agreement_score(model_a: float | None, model_b: float | None, tolerance: float = 0.15) -> float:
    """1.0 = modelos coincidem; 0.0 = diferem pelo menos `tolerance`. Sem segundo modelo: 0.5 (neutro)."""
    if model_a is None or model_b is None:
        return 0.5
    return _unit(1.0 - abs(float(model_a) - float(model_b)) / tolerance)


def classify_confidence(
    *,
    data_quality: float,
    market_validated: bool,
    validation_gain: float | None,
    validation_samples: int,
    calibration_state: str,          # "VALIDATED" | "FITTED" | "NONE"
    model_agreement: float,
    edge: float,
) -> ConfidenceResult:
    data_part = _unit(data_quality)
    gain = max(0.0, validation_gain or 0.0)
    validation_part = (min(gain / 0.03, 1.0) * min(validation_samples / 150.0, 1.0)) if market_validated else 0.0
    calibration_part = {"VALIDATED": 1.0, "FITTED": 0.5}.get(calibration_state, 0.0)
    agreement_part = _unit(model_agreement)
    edge_part = _unit(max(edge, 0.0) / 0.10)

    score = (W_DATA * data_part + W_VALIDATION * validation_part + W_CALIBRATION * calibration_part
             + W_AGREEMENT * agreement_part + W_EDGE * edge_part)

    reasons: list[str] = []
    if not market_validated:
        reasons.append("o mercado ainda não foi validado fora da amostra")
    if data_part < 0.6:
        reasons.append("poucos jogos históricos de uma das equipas")
    if calibration_state == "NONE":
        reasons.append("probabilidades ainda não calibradas")
    if agreement_part < 0.5:
        reasons.append("os modelos discordam entre si")

    # Condições obrigatórias: sem validação do mercado nunca passa de BAIXA.
    if market_validated and score >= 0.70 and data_part >= 0.75 and validation_samples >= 100 and calibration_state == "VALIDATED":
        level = HIGH
    elif market_validated and score >= 0.45:
        level = MODERATE
    else:
        level = LOW
    return ConfidenceResult(level, round(score, 4), reasons)
